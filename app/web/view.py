"""Template view helpers.

Kept out of the routers so every page gets the same context. The important one
is ``league_context``: it supplies the topbar's league name and the signed-in
player's position, which is what keeps "where am I" to a single glance.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scoring import rank
from app.models import Draft, Event, League, Player, Score, Team


async def league_context(
    db: AsyncSession,
    league: League,
    player: Player | None,
    *,
    csrf_token: str | None = None,
) -> dict:
    """Everything the base template needs, computed once per request.

    ``csrf_token`` is a parameter rather than something stashed on the session
    object. It used to come from ``db.info``, and every router spread this dict
    *after* its own ``"csrf_token"`` key, so the None here silently overwrote
    the real token and every form rendered value="None".
    """
    ctx: dict = {
        "league": league,
        "player": player,
        "my_team": None,
        "my_position": None,
        "my_points": None,
        "team_count": 0,
        "is_admin": False,
        "csrf_token": csrf_token,
    }

    ctx["team_count"] = (
        await db.scalar(select(func.count(Team.id)).where(Team.league_id == league.id)) or 0
    )

    if player is None:
        return ctx

    ctx["is_admin"] = league.admin_player_id == player.id

    team = await db.scalar(
        select(Team).where(Team.league_id == league.id, Team.player_id == player.id)
    )
    ctx["my_team"] = team
    return ctx


async def standings_for(
    db: AsyncSession, league: League, player: Player | None
) -> tuple[list, Team | None]:
    """Season leaderboard with gap-to-leader and movement.

    ``player`` only decides which row is highlighted — it changes nothing about
    who is visible.
    """
    rows = (
        await db.execute(
            select(
                Team.id,
                Team.display_name,
                func.coalesce(func.sum(Score.points), 0),
            )
            .outerjoin(Score, Score.team_id == Team.id)
            .where(Team.league_id == league.id)
            .group_by(Team.id, Team.display_name)
        )
    ).all()

    ranked = rank([(str(tid), name, float(total or 0)) for tid, name, total in rows])

    previous = await _previous_positions(db, league)
    if previous:
        ranked = rank([(r.team_id, r.display_name, r.points) for r in ranked], previous=previous)

    my_team = None
    if player is not None:
        my_team = await db.scalar(
            select(Team).where(Team.league_id == league.id, Team.player_id == player.id)
        )

    return ranked, my_team


async def _previous_positions(db: AsyncSession, league: League) -> dict[str, int]:
    """Positions before the most recent round, for the trend arrows.

    Scoped to races only: a sprint is a separate event, and mixing them would
    make every movement arrow wrong.
    """

    def races(rounds_below: int | None = None):
        clause = (Event.league_id == league.id) & (Event.kind == "race")
        if rounds_below is not None:
            clause = clause & (Event.round <= rounds_below)
        return select(func.max(Event.round)).where(clause).scalar_subquery()

    latest = await db.scalar(select(races()))
    if latest is None:
        return {}

    previous_round = await db.scalar(select(races(latest - 1)))
    if previous_round is None:
        return {}

    rows = (
        await db.execute(
            select(Team.id, func.sum(Score.points))
            .join(Score, Score.team_id == Team.id)
            .join(Event, Event.id == Score.event_id)
            .where(
                Event.league_id == league.id,
                Event.kind == "race",
                Event.round == previous_round,
            )
            .group_by(Team.id)
        )
    ).all()

    ordered = sorted(rows, key=lambda r: float(r[1] or 0), reverse=True)
    return {str(team_id): pos for pos, (team_id, _) in enumerate(ordered, start=1)}


async def draft_state(db: AsyncSession, league: League) -> Draft | None:
    return await db.get(Draft, league.id)


def remaining_seconds(draft: Draft | None) -> int | None:
    """Seconds left on the current pick, or None when nothing is armed.

    Read from the database rather than tracked in memory — that is the whole
    design. Nothing here schedules anything.
    """
    if draft is None or draft.pick_expires_at is None:
        return None

    from app.clock import SystemClock

    delta = (draft.pick_expires_at - SystemClock().now()).total_seconds()
    return max(0, int(delta))
