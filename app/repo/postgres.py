"""Postgres repositories.

The only layer that talks SQL. Everything above it works through Protocols, which
is what lets the draft turn machine be tested with an in-memory fake rather than
a container.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock, SystemClock
from app.domain import draft as domain_draft
from app.models import Draft, Driver, League, Player, Roster, Team
from app.notifier import LeagueContext, Notifier, NullNotifier
from app.services.draft import DraftRepository


class PostgresDraftRepo(DraftRepository):
    """Implements the five-method contract the turn machine depends on."""

    def __init__(self, session: AsyncSession, clock: Clock | None = None) -> None:
        self.s = session
        self.clock = clock or SystemClock()

    async def get_state(self, league_id: UUID) -> domain_draft.DraftState[UUID] | None:
        row = await self.s.get(Draft, league_id)
        if row is None:
            return None

        team_ids = await self._team_ids(league_id)
        drivers = await self._season_driver_count(league_id)
        rounds = max(1, len(team_ids))
        size = len(row.pick_order) // rounds if team_ids else 0

        return domain_draft.DraftState(
            status=domain_draft.DraftStatus(row.status),
            current_pick=row.current_pick,
            # JSONB holds strings, not UUID objects, so rehydrate on read.
            pick_order=tuple(UUID(str(t)) for t in (row.pick_order or ())),
            team_ids=tuple(team_ids),
            season_drivers=drivers,
            team_size=size,
        )

    async def get_expiry(self, league_id: UUID) -> datetime | None:
        row = await self.s.get(Draft, league_id)
        return row.pick_expires_at if row else None

    async def get_team_ids(self, league_id: UUID) -> list[UUID]:
        return await self._team_ids(league_id)

    async def get_taken_driver_ids(self, league_id: UUID) -> set[UUID]:
        rows = await self.s.scalars(select(Roster.driver_id).where(Roster.league_id == league_id))
        return set(rows.all())

    async def get_available_drivers(self, league_id: UUID) -> list[tuple[UUID, str, str]]:
        """Unpicked drivers for this league's season.

        The season comes from the league row rather than an argument. An
        argument here once carried a driver *count*, which silently filtered on
        ``season_year = 20`` and returned nothing.
        """
        league = await self.s.get(League, league_id)
        year = league.season_year if league else 0

        rows = await self.s.execute(
            select(Driver.id, Driver.name, Driver.constructor)
            .where(
                Driver.season_year == year,
                Driver.id.not_in(select(Roster.driver_id).where(Roster.league_id == league_id)),
            )
            .order_by(Driver.constructor, Driver.name)
        )
        return [(r[0], r[1], r[2]) for r in rows.all()]

    async def commit_pick(
        self,
        league_id: UUID,
        team_id: UUID,
        driver_id: UUID,
        pick_number: int,
        auto: bool,
    ) -> None:
        """Insert the pick.

        The roster primary key is the concurrency guard: if two players race for
        the same driver, this raises IntegrityError and the caller reports it.
        No SELECT-then-INSERT check, because that would be a race.

        Wrapped in a savepoint so the caller can undo just this insert. Rolling
        back the whole session would also discard the draft cursor and the
        expiry this pick had already advanced, leaving the draft stuck.
        """
        async with self.s.begin_nested():
            self.s.add(
                Roster(
                    league_id=league_id,
                    team_id=team_id,
                    driver_id=driver_id,
                    pick_number=pick_number,
                    auto_picked=auto,
                )
            )
            await self.s.flush()

    async def save_state(self, league_id: UUID, state: domain_draft.DraftState[UUID]) -> None:
        row = await self.s.get(Draft, league_id)
        if row is None:
            row = Draft(league_id=league_id)
            self.s.add(row)

        row.status = str(state.status)
        row.current_pick = state.current_pick
        # JSONB serialises ids as strings.
        row.pick_order = [str(t) for t in state.pick_order]
        row.total_picks = state.total_picks
        await self.s.flush()

    async def set_expiry(self, league_id: UUID, expires_at: datetime) -> None:
        """Arm the pick timer.

        Goes through the ORM rather than raw SQL on purpose: a raw UPDATE leaves
        the session's identity map holding a stale Draft, so the next
        get_expiry() would read the previous deadline and the timeout would
        never fire. The draft turn machine depends on this being coherent
        within a single request.
        """
        row = await self.s.get(Draft, league_id)
        if row is None:
            row = Draft(league_id=league_id)
            self.s.add(row)
        row.pick_expires_at = expires_at
        await self.s.flush()

    async def team_display_names(self, league_id: UUID) -> dict[UUID, str]:
        rows = await self.s.execute(
            select(Team.id, Team.display_name).where(Team.league_id == league_id)
        )
        return {r[0]: r[1] for r in rows.all()}

    async def _team_ids(self, league_id: UUID) -> list[UUID]:
        rows = await self.s.scalars(
            select(Team.id)
            .where(Team.league_id == league_id)
            .order_by(func.coalesce(Team.draft_order, 9999), Team.joined_at)
        )
        return list(rows.all())

    async def _season_driver_count(self, league_id: UUID) -> int:
        league = await self.s.get(League, league_id)
        if league is None:
            return 0
        return await self.s.scalar(
            select(func.count(Driver.id)).where(Driver.season_year == league.season_year)
        )


class LeagueRepo:
    """League and player lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create_league(
        self,
        *,
        code: str,
        name: str,
        season_year: int,
        admin: Player,
        pick_deadline: int = 600,
        team_size: int | None = None,
        webhook_id: str | None = None,
        webhook_token: str | None = None,
    ) -> League:
        league = League(
            code=code,
            name=name,
            season_year=season_year,
            admin_player_id=admin.id,
            pick_deadline=pick_deadline,
            team_size=team_size,
            webhook_id=webhook_id,
            webhook_token=webhook_token,
            state="created",
        )
        self.s.add(league)
        await self.s.flush()

        self.s.add(Draft(league_id=league.id, status="pending"))
        return league

    async def upsert_player(
        self,
        *,
        provider: str,
        external_id: str | None,
        display_name: str,
    ) -> Player:
        """Resolve an identity to a player row.

        Guests have no external id, so they always get a fresh row — which is
        correct: two guests with the same display name are two players.
        """
        if provider == "discord" and external_id:
            existing = await self.s.scalar(
                select(Player).where(
                    Player.provider == "discord", Player.external_id == external_id
                )
            )
            if existing is not None:
                existing.display_name = display_name
                await self.s.flush()
                return existing

        player = Player(provider=provider, external_id=external_id, display_name=display_name)
        self.s.add(player)
        await self.s.flush()
        return player

    async def join(self, league_id: UUID, player: Player) -> Team | None:
        """Add a player to the league. Returns None if already joined."""
        existing = await self.s.scalar(
            select(Team).where(Team.league_id == league_id, Team.player_id == player.id)
        )
        if existing is not None:
            return None

        team = Team(league_id=league_id, player_id=player.id, display_name=player.display_name)
        self.s.add(team)
        await self.s.flush()
        return team

    async def set_draft_order(self, league_id: UUID, order: list[UUID]) -> None:
        """Persist the randomised seed order through the ORM (see set_expiry)."""
        rows = (await self.s.scalars(select(Team).where(Team.league_id == league_id))).all()
        position_of = {team_id: pos for pos, team_id in enumerate(order, start=1)}
        for team in rows:
            team.draft_order = position_of.get(team.id)
        await self.s.flush()

    async def set_state(self, league_id: UUID, state: str) -> None:
        league = await self.get(league_id)
        if league is not None:
            league.state = state
            await self.s.flush()

    async def get(self, league_id: UUID) -> League | None:
        return await self.s.get(League, league_id)

    async def by_code(self, code: str) -> League | None:
        return await self.s.scalar(select(League).where(League.code == code))

    async def rosters(self, league_id: UUID) -> dict[str, list[str]]:
        rows = await self.s.execute(
            select(Team.display_name, Driver.name, Roster.pick_number)
            .join(Roster, Roster.team_id == Team.id)
            .join(Driver, Driver.id == Roster.driver_id)
            .where(Roster.league_id == league_id)
            .order_by(Team.display_name, Roster.pick_number)
        )
        out: dict[str, list[str]] = {}
        for name, driver, _ in rows.all():
            out.setdefault(name, []).append(driver)
        return out

    async def is_admin(self, league_id: UUID, player: Player) -> bool:
        league = await self.get(league_id)
        return league is not None and league.admin_player_id == player.id

    @staticmethod
    def context(league: League) -> LeagueContext:
        return LeagueContext(
            id=league.id,
            code=league.code,
            name=league.name,
            season_year=league.season_year,
            webhook_id=league.webhook_id,
            webhook_token=league.webhook_token,
        )

    @staticmethod
    def notifier_for(league: League) -> Notifier:
        """Pick a notifier from configuration.

        A league with no webhook is still fully playable — it just announces
        nowhere. This is what makes the non-Discord deployment work.
        """
        from app.discord.webhook import DiscordNotifier

        if league.webhook_id and league.webhook_token:
            return DiscordNotifier(LeagueRepo.context(league))
        return NullNotifier()
