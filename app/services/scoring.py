"""Scoring service: fetch real results, persist them, announce them.

Idempotent by construction — every insert is an upsert keyed on event identity,
so running the scorer twice for the same round is a no-op rather than a
double-count. That matters because Railway cron can retry, and because the
admin panel offers a force-rescore button.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.scoring import EventKind as DomainKind
from app.domain.scoring import Result as DomainResult
from app.domain.scoring import rank, score
from app.models import Driver, Event, League, Score
from app.models import Result as ResultRow
from app.provider.base import F1Provider, Kind, ProviderError, Status
from app.provider.jolpica import JolpicaProvider
from app.repo.postgres import LeagueRepo

log = logging.getLogger(__name__)


async def seed_season(
    session: AsyncSession, provider: F1Provider, season: int, *, starters_only: bool = True
) -> int:
    """Load the driver grid for a season. Idempotent on (season_year, code).

    With ``starters_only`` the grid is narrowed to drivers who actually appear in
    a race result. A season endpoint returns ~36 entries including reserves and
    test drivers; drafting them would fill rosters with cars that can never
    score.
    """
    drivers = await provider.drivers(season)

    if starters_only:
        starters = await _starting_codes(provider, season)
        if starters:
            narrowed = [d for d in drivers if d.code in starters]
            if narrowed:
                drivers = narrowed
    for driver in drivers:
        stmt = (
            pg_insert(Driver)
            .values(
                season_year=season,
                code=driver.code,
                name=driver.name,
                constructor=driver.constructor,
            )
            .on_conflict_do_update(
                index_elements=["season_year", "code"],
                set_={"name": driver.name, "constructor": driver.constructor},
            )
        )
        await session.execute(stmt)
    await session.commit()
    return len(drivers)


async def _starting_codes(provider: F1Provider, season: int) -> set[str]:
    """Codes that appear in a completed race. Falls back to the full grid."""
    from app.provider.base import ProviderError

    for round_number in (1, 2, 3):
        try:
            result = await provider.event_result(season, round_number, Kind.RACE)
        except ProviderError:
            continue
        if result.results:
            return {r.code for r in result.results if r.code}
    return set()


async def score_league_event(
    session: AsyncSession,
    league: League,
    round_number: int,
    kind: Kind,
    provider: F1Provider,
) -> tuple[int, list[tuple[str, float]]] | None:
    """Score one event for one league. Returns None when results are unavailable.

    Idempotent: the event upsert is keyed on identity and prior rows for that
    event are cleared first, so re-running replaces rather than appends.
    """
    try:
        upstream = await provider.event_result(league.season_year, round_number, kind)
    except ProviderError as exc:
        log.info("no %s data for R%s: %s", kind, round_number, exc)
        return None

    stmt = (
        pg_insert(Event)
        .values(
            league_id=league.id,
            season_year=league.season_year,
            round=round_number,
            kind=str(kind),
            name=upstream.name,
            scored_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="event_identity_key",
            set_={"name": upstream.name, "scored_at": func.now()},
        )
        .returning(Event.id)
    )
    event_id = (await session.execute(stmt)).scalar_one()
    await session.commit()

    return await _persist_and_standings(session, league, event_id, kind, upstream)


async def _persist_and_standings(
    session: AsyncSession,
    league: League,
    event_id: UUID,
    kind: Kind,
    upstream,
) -> tuple[int, list[tuple[str, float]]]:
    """Write results and scores for one event, then read back its standings."""
    from app.models import Driver, Roster

    drivers = {
        row.code: row.id
        for row in (
            await session.scalars(select(Driver).where(Driver.season_year == league.season_year))
        ).all()
    }

    # Clear prior rows so a re-score overwrites rather than duplicates.
    await session.execute(delete(ResultRow).where(ResultRow.event_id == event_id))
    await session.execute(delete(Score).where(Score.event_id == event_id))

    for row in upstream.results:
        driver_id = drivers.get(row.code)
        if driver_id is not None:
            session.add(
                ResultRow(
                    event_id=event_id,
                    driver_id=driver_id,
                    position=row.position,
                    grid=row.grid,
                    quali=row.quali,
                    status=str(row.status),
                    fastest_lap=row.fastest_lap,
                )
            )

    # Which teams own which drivers.
    ownership: dict[UUID, list[UUID]] = {}
    for driver_id, team_id in (
        await session.execute(
            select(Roster.driver_id, Roster.team_id).where(Roster.league_id == league.id)
        )
    ).all():
        ownership.setdefault(driver_id, []).append(team_id)

    domain_kind = DomainKind.SPRINT if kind is Kind.SPRINT else DomainKind.RACE

    for row in upstream.results:
        driver_id = drivers.get(row.code)
        if driver_id is None:
            continue

        breakdown = score(
            DomainResult(
                grid=row.grid,
                position=row.position,
                dnf=not row.status.classified and row.status is not Status.DSQ,
                dsq=row.status is Status.DSQ,
                fastest_lap=row.fastest_lap,
                quali=row.quali,
            ),
            domain_kind,
        )

        for team_id in ownership.get(driver_id, []):
            session.add(
                Score(
                    event_id=event_id,
                    team_id=team_id,
                    driver_id=driver_id,
                    points=breakdown.total,
                    breakdown=breakdown.as_dict(),
                )
            )

    await session.commit()

    standings = await event_standings(session, event_id)
    return len(standings), standings


async def event_standings(session: AsyncSession, event_id: UUID) -> list[tuple[str, float]]:
    from app.models import Team

    rows = (
        await session.execute(
            select(
                Team.id,
                Team.display_name,
                func.coalesce(func.sum(Score.points), 0),
            )
            .join(Score, Score.team_id == Team.id)
            .where(Score.event_id == event_id)
            .group_by(Team.id, Team.display_name)
        )
    ).all()

    ranked = rank([(str(team_id), name, float(total or 0)) for team_id, name, total in rows])
    return [(r.display_name, r.points) for r in ranked]


async def season_standings(session: AsyncSession, league_id: UUID) -> list:
    from app.models import Team

    rows = (
        await session.execute(
            select(Team.id, Team.display_name, func.coalesce(func.sum(Score.points), 0))
            .outerjoin(Score, Score.team_id == Team.id)
            .where(Team.league_id == league_id)
            .group_by(Team.id, Team.display_name)
        )
    ).all()

    return rank([(str(team_id), name, float(total or 0)) for team_id, name, total in rows])


async def score_recent_events(
    engine,
    *,
    window_hours: int = 36,
    provider: F1Provider | None = None,
) -> int:
    """Score every league whose window overlaps a recently finished event.

    This is what the Railway cron service calls. Safe to run repeatedly.
    """
    owns_provider = provider is None
    provider = provider or JolpicaProvider()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    scored_total = 0
    try:
        async with session_factory() as session:
            leagues = (await session.scalars(select(League))).all()
            if not leagues:
                return 0

            seasons = {lg.season_year for lg in leagues}
            calendars: dict[int, list] = {}
            for season in seasons:
                try:
                    calendars[season] = await provider.calendar(season)
                except ProviderError as exc:
                    log.warning("calendar unavailable for %s: %s", season, exc)
                    calendars[season] = []

            now = datetime.now(UTC)
            window_start = now - timedelta(hours=window_hours)

            for league in leagues:
                calendar = calendars.get(league.season_year, [])
                for race in calendar:
                    candidates: list[Kind] = [Kind.RACE]
                    if race.sprint_date:
                        candidates.append(Kind.SPRINT)

                    for kind in candidates:
                        when = _parse(race.sprint_date if kind is Kind.SPRINT else race.date)
                        if when is None or not (window_start <= when <= now):
                            continue

                        async with session_factory() as league_session:
                            repo = LeagueRepo(league_session)
                            fresh = await repo.get(league.id)
                            if fresh is None:
                                continue

                            already = await league_session.scalar(
                                select(Event.id).where(
                                    Event.league_id == fresh.id,
                                    Event.season_year == fresh.season_year,
                                    Event.round == race.round,
                                    Event.kind == str(kind),
                                )
                            )
                            if already is not None:
                                continue

                            outcome = await score_league_event(
                                league_session, fresh, race.round, kind, provider
                            )
                            if outcome is None:
                                continue

                            _, standings = outcome
                            scored_total += 1
                            notifier = repo.notifier_for(fresh)
                            await notifier.results_posted(
                                repo.context(fresh),
                                race.name,
                                str(kind),
                                standings,
                            )
        return scored_total
    finally:
        if owns_provider:
            await provider.aclose()  # type: ignore[attr-defined]


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
