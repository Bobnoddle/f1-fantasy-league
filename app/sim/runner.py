"""League simulator.

Drives a real league end to end against a real database and real F1 data:

    signup -> join -> draft -> full season of scoring

Nothing here is faked except the players. The draft goes through the same
``DraftService`` the web app uses, so every constraint — the roster primary key,
turn order, lazy expiry — is genuinely exercised. Two agents racing for the same
driver will produce a real IntegrityError.

Time is virtual. ``SimulatedClock`` lets a 24-round season replay in under a
minute, which is what makes "progress the league using real data" practical.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock, SimulatedClock
from app.models import Event, League, Roster, Team
from app.notifier import ConsoleNotifier, Notifier
from app.provider.base import F1Provider, Kind, ProviderError
from app.repo.postgres import LeagueRepo, PostgresDraftRepo
from app.services.draft import DraftService
from app.services.scoring import score_league_event, season_standings
from app.sim.agents import DriverView, SimAgent, default_roster

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SimConfig:
    """Everything the simulation needs. Explicit so runs are reproducible."""

    league_code: str = "sim"
    league_name: str = "Simulated League"
    players: int = 6
    pick_deadline: int = 600
    team_size: int | None = None
    seed: int = 7
    time_compression: float = 3600.0  # 1 real second = 1 virtual hour
    discord: bool = False  # False = console only (non-Discord run)
    verbose: bool = True


@dataclass(slots=True)
class SimReport:
    """What the run produced. Returned so tests and CI can assert on it."""

    league_id: UUID
    picks: int = 0
    auto_picks: int = 0
    races_scored: int = 0
    sprints_scored: int = 0
    events_skipped: int = 0
    rosters: dict[str, list[str]] = field(default_factory=dict)
    final_standings: list = field(default_factory=list)
    integrity_conflicts: int = 0
    total_picks: int = 0


class Simulator:
    def __init__(
        self,
        session: AsyncSession,
        provider: F1Provider,
        config: SimConfig,
        *,
        clock: Clock | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.s = session
        self.provider = provider
        self.cfg = config
        self.clock = clock or SimulatedClock(speed=config.time_compression)
        self.rng = random.Random(config.seed)
        self.repo = LeagueRepo(session)
        self.draft_repo = PostgresDraftRepo(session, clock=self.clock)
        self.draft = DraftService(
            self.draft_repo,
            notifier or ConsoleNotifier(self.cfg.verbose),
            clock=self.clock,
        )
        self.agents: list[SimAgent] = []
        self.report = SimReport(league_id=UUID(int=0))

    # ── Setup ────────────────────────────────────────────────────────────────

    async def setup(self, season: int, names: list[str] | None = None) -> League:
        from app.services.scoring import seed_season

        if names is None:
            names = [f"Player {i + 1}" for i in range(self.cfg.players)]

        drivers = await seed_season(self.s, self.provider, season)
        self.grid_size = drivers  # real starters, not reserves
        self._say(f"seeded {drivers} starting drivers for {season}")

        # Admin is a guest player — no Discord identity anywhere.
        admin = await self.repo.upsert_player(
            provider="guest", external_id=None, display_name="League Admin"
        )

        league = await self.repo.create_league(
            code=self.cfg.league_code,
            name=self.cfg.league_name,
            season_year=season,
            admin=admin,
            pick_deadline=self.cfg.pick_deadline,
            team_size=self.cfg.team_size,
        )
        self.report.league_id = league.id
        await self._announce(league, "signup_updated", 0, 0)

        agents = default_roster(names, seed=self.cfg.seed)
        for agent in agents:
            player = await self.repo.upsert_player(
                provider="guest", external_id=None, display_name=agent.name
            )
            await self.repo.join(league.id, player)

        count = len(agents)
        await self.repo.set_state(league.id, "draft_ready")
        await self._announce(league, "signup_updated", count, self._team_size(count))
        self.agents = agents
        self._say(f"{count} players joined")
        return league

    def _team_size(self, players: int) -> int:
        from app.domain.draft import team_size

        # Driver count is the real 2026 grid size; matches what seeding loaded.
        return team_size(players, getattr(self, "grid_size", 22), self.cfg.team_size)

    # ── Draft ────────────────────────────────────────────────────────────────

    async def run_draft(self, league: League) -> SimReport:
        """Play the draft out, agent by agent.

        An agent may deliberately blow its deadline. That is the point: the
        timeout path is the easiest thing to break and the hardest to test by
        hand.
        """
        await self.repo.set_state(league.id, "drafting")
        await self.draft.start(
            league.id,
            season_drivers=getattr(self, "grid_size", 22),
            size_override=self.cfg.team_size,
            deadline_secs=self.cfg.pick_deadline,
        )

        names = await self.draft_repo.team_display_names(league.id)
        by_team = {team_id: self._agent_for(display) for team_id, display in names.items()}

        guard = 0
        max_turns = 500
        while guard < max_turns:
            guard += 1

            state = await self.draft_repo.get_state(league.id)
            if state is None or state.status.value != "drafting":
                break

            on_clock = state.on_the_clock
            if on_clock is None:
                break

            agent = by_team.get(on_clock) or SimAgent(str(on_clock)[:8])

            if agent.will_timeout(self.cfg.pick_deadline, agent.rng):
                # An agent that blows its deadline must actually cross it.
                # Advancing only to its own delay (85% of the window at most)
                # leaves the pick open, so the timeout path never runs.
                self.clock.advance(self.cfg.pick_deadline + 5)  # type: ignore[union-attr]
                outcome = await self.draft.advance_if_expired(
                    league.id,
                    getattr(self, "grid_size", 22),
                    self.cfg.pick_deadline,
                    display_names=names,
                )
                if outcome is not None:
                    self.report.auto_picks += 1
                    self.report.picks += 1
                continue

            # Well inside the window, so the pick is honoured.
            self.clock.advance(  # type: ignore[union-attr]
                agent.delay(self.cfg.pick_deadline, agent.rng)
            )

            picked = await self._pick_for(league, on_clock, agent, names)
            if not picked:
                break

        self.report.total_picks = guard
        self.report.rosters = await self.repo.rosters(league.id)
        self.report.final_standings = await season_standings(self.s, league.id)
        await self.draft.publisher.draft_complete(self.repo.context(league), self.report.rosters)
        return self.report

    def _agent_for(self, display_name: str) -> SimAgent:
        for agent in getattr(self, "agents", []):
            if agent.name == display_name:
                return agent
        return SimAgent(display_name)

    async def _pick_for(
        self, league: League, team_id: UUID, agent: SimAgent, names: dict[UUID, str]
    ) -> bool:
        available = await self.draft_repo.get_available_drivers(league.id)
        taken = await self.draft_repo.get_taken_driver_ids(league.id)
        pool = [
            DriverView(id=did, name=name, constructor=team)
            for did, name, team in available
            if did not in taken
        ]
        if not pool:
            return False

        owned = await self._owned_by_team(league.id)
        choice = agent.choose(pool, owned, agent.rng)

        try:
            outcome = await self.draft.pick(
                league.id,
                team_id,
                choice.id,
                self.cfg.pick_deadline,
                display_names=names,
            )
        except IntegrityError:
            # The storage-layer guard fired. Real, and exactly what it is for.
            await self.s.rollback()
            self.report.integrity_conflicts += 1
            self._say("   ! double-pick blocked by roster primary key")
            return True

        self.report.picks += 1
        if outcome.auto_picked:
            self.report.auto_picks += 1
        return True

    async def _owned_by_team(self, league_id: UUID) -> dict[str, list[str]]:
        from app.models import Driver

        rows = (
            await self.s.execute(
                select(Team.display_name, Driver.constructor)
                .join(Roster, Roster.team_id == Team.id)
                .join(Driver, Driver.id == Roster.driver_id)
                .where(Roster.league_id == league_id)
            )
        ).all()
        owned: dict[str, list[str]] = {}
        for name, constructor in rows:
            owned.setdefault(name, []).append(constructor)
        return owned

    # ── Season ───────────────────────────────────────────────────────────────

    async def run_season(
        self,
        league: League,
        *,
        through_round: int | None = None,
    ) -> SimReport:
        """Score real race results round by round.

        Unlike the production cron this walks rounds directly rather than
        watching a time window, because a past season's races are all in the
        past and the window would never match.
        """
        try:
            calendar = await self.provider.calendar(league.season_year)
        except ProviderError as exc:
            self._say(f"no calendar for {league.season_year}: {exc}")
            return self.report

        for race in calendar:
            if through_round is not None and race.round > through_round:
                break

            await self._score_one(league, race.round, Kind.RACE)
            if race.sprint_date:
                await self._score_one(league, race.round, Kind.SPRINT)

        await self.repo.set_state(league.id, "active")
        self.report.final_standings = await season_standings(self.s, league.id)
        return self.report

    async def _score_one(self, league: League, round_number: int, kind: Kind) -> None:
        existing = await self.s.scalar(
            select(Event.id).where(
                Event.league_id == league.id,
                Event.season_year == league.season_year,
                Event.round == round_number,
                Event.kind == str(kind),
            )
        )
        if existing is not None:
            self.report.events_skipped += 1
            return

        outcome = await score_league_event(self.s, league, round_number, kind, self.provider)
        if outcome is None:
            self.report.events_skipped += 1
            self._say(f"   · R{round_number} {kind}: no data")
            return

        if kind is Kind.SPRINT:
            self.report.sprints_scored += 1
        else:
            self.report.races_scored += 1

        count, standings = outcome
        self._say(f"   · R{round_number} {kind}: {count} teams scored")
        await self._announce_results(league, round_number, kind, standings)

    async def _announce_results(
        self, league: League, round_number: int, kind: Kind, standings
    ) -> None:
        notifier = self.repo.notifier_for(league)
        if self.cfg.discord:
            await notifier.results_posted(
                self.repo.context(league), f"R{round_number}", str(kind), standings
            )
        elif self.cfg.verbose:
            event = await self.s.scalar(
                select(Event).where(
                    Event.league_id == league.id,
                    Event.round == round_number,
                    Event.kind == str(kind),
                )
            )
            await ConsoleNotifier().results_posted(
                self.repo.context(league),
                event.name if event else f"R{round_number}",
                str(kind),
                standings,
            )

    # ── Reporting ────────────────────────────────────────────────────────────

    async def summarise(self, league: League) -> str:
        standings = await season_standings(self.s, league.id)
        lines = [
            f"\n{'=' * 58}",
            f"  {league.name} — {league.season_year}",
            f"{'=' * 58}",
            f"  picks            {self.report.picks}"
            f"  ({self.report.auto_picks} auto, "
            f"{self.report.integrity_conflicts} conflicts blocked)",
            f"  races scored     {self.report.races_scored}",
            f"  sprints scored   {self.report.sprints_scored}",
            f"  events skipped   {self.report.events_skipped}",
            f"{'-' * 58}",
        ]
        for row in standings:
            lines.append(
                f"  {row.position:>2}. {row.display_name:<16} {row.points:>7.0f} pts"
                + (f"   ({row.trend:+d})" if row.trend else "")
            )
        lines.append(f"{'=' * 58}\n")
        return "\n".join(lines)

    async def _announce(self, league: League, method: str, *args) -> None:
        if self.cfg.verbose:
            await getattr(ConsoleNotifier(), method)(self.repo.context(league), *args)

    def _say(self, message: str) -> None:
        if self.cfg.verbose:
            print(message, flush=True)
