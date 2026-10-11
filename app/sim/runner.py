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

import asyncio
import logging
import random
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock, SimulatedClock
from app.models import Event, League, Roster, Team
from app.notifier import ConsoleNotifier, Notifier
from app.provider.base import F1Provider, Kind, ProviderError
from app.repo.postgres import LeagueRepo, PostgresDraftRepo
from app.services.draft import DraftService
from app.services.scoring import score_league_event, season_standings, seed_season
from app.sim.agents import DriverView, SimAgent, bot_names, default_roster

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

    #: Add bots to a league that already exists instead of creating one. The
    #: human keeps their seat, and the draft waits for them to pick.
    attach: str | None = None

    #: How long a human team may sit on the clock before the pick is settled for
    #: them, in real seconds. Independent of ``pick_deadline``, which is measured
    #: on the simulated clock.
    human_grace: int = 900

    #: Stop after signing the bots up, leaving the draft untouched. Signup is a
    #: thing the admin controls from the panel, so attaching should not silently
    #: consume it.
    signup_only: bool = False


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

    async def attach(self, code: str, season: int) -> League:
        """Add bots to a league the human already created.

        The point of playing a league locally: you create it in the browser, join
        it yourself, then run this to fill it with bots and play the season. The
        existing league is reused — never deleted — so the human keeps their team
        and their admin rights.

        The human is not an agent, so the draft hands the clock to them and waits
        rather than picking for them.
        """
        league = await self.repo.by_code(code)
        if league is None:
            raise LookupError(
                f"No league called {code!r}. Create it at /signup first, "
                "or drop --attach to have the simulator make one."
            )

        if league.season_year != season:
            raise ValueError(
                f"League {code!r} is for {league.season_year}, not {season}. "
                f"Re-run with --season {league.season_year}, or change it in the "
                "admin panel."
            )

        drivers = await seed_season(self.s, self.provider, season)
        self.grid_size = drivers
        self._say(f"seeded {drivers} starting drivers for {season}")

        bots = default_roster(bot_names(self.cfg.players), seed=self.cfg.seed)

        added = 0
        for agent in bots:
            player = await self._bot_player(league, agent.name)
            if await self.repo.join(league.id, player) is not None:
                added += 1

        self.agents = bots
        await self.repo.set_state(league.id, "draft_ready")
        total = await self.s.scalar(select(func.count(Team.id)).where(Team.league_id == league.id))
        await self._announce(league, "signup_updated", total or 0, self._team_size(total or 0))

        self.report.league_id = league.id
        self._say(f"{added} bot(s) added to {league.name!r}; {total} teams in the league")
        if added == 0:
            self._say("every bot was already in the league — nothing added")
        return league

    async def _bot_player(self, league: League, name: str):
        """The bot called ``name``, creating it only if it is not here yet.

        upsert_player deliberately makes a new player every time for a guest,
        because two humans who pick the same display name are two people. That
        is right for guests and wrong for bots: re-attaching would add a second
        "Bot 1" alongside the first. So bots are matched on their name within
        this league.
        """
        existing = await self.s.scalar(
            select(Team.player_id).where(Team.league_id == league.id, Team.display_name == name)
        )
        if existing is not None:
            from app.models import Player

            found = await self.s.get(Player, existing)
            if found is not None:
                return found

        return await self.repo.upsert_player(provider="guest", external_id=None, display_name=name)

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

        # Teams nobody here is simulating. When attaching to a league the human
        # made, that is their team: the draft must hand them the clock and wait,
        # not pick on their behalf.
        simulated = {agent.name for agent in self.agents}
        human_teams = {t for t, display in names.items() if display not in simulated}
        if human_teams:
            who = ", ".join(sorted(names[t] for t in human_teams))
            self._say(f"leaving the clock to: {who}")

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

            if on_clock in human_teams:
                # Not ours to pick. Wait for the human to open the picker and
                # choose, and if they wander off, settle the pick for them so
                # the draft still finishes.
                if await self._await_human(league, on_clock, names):
                    self.report.picks += 1
                await self.s.commit()
                continue

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
                await self.s.commit()
                continue

            # Well inside the window, so the pick is honoured.
            self.clock.advance(  # type: ignore[union-attr]
                agent.delay(self.cfg.pick_deadline, agent.rng)
            )

            picked = await self._pick_for(league, on_clock, agent, names)
            if not picked:
                break

            # Commit every turn.
            #
            # The web app reads on its own connection, so an uncommitted draft is
            # invisible to it: the human's own page still showed a pending draft
            # and refused to open the picker. The web layer deliberately flushes
            # and lets its middleware commit, but a long-running CLI owns its
            # transaction outright.
            await self.s.commit()

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

    async def _await_human(self, league: League, team_id, names: dict) -> bool:
        """Hand the clock to a human team and wait for their pick.

        Polls rather than sleeping on a fixed delay, so a pick made in the
        browser is picked up immediately. The pick itself is written by the web
        request — this only watches for the cursor to move.

        If they do nothing within the grace window the pick is settled for them,
        which is the same path a real timeout takes, so walking away from the
        draft still produces a complete roster.
        """
        import time

        who = names.get(team_id, "someone")
        self._say(f"   · waiting for {who} to pick in the browser…")

        deadline = time.monotonic() + self.cfg.human_grace
        poll = 0.5
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            state = await self.draft_repo.get_state(league.id)
            if state is None or state.on_the_clock != team_id:
                self._say(f"   · {who} picked")
                return True
            if state.status.value != "drafting":
                return False

        self._say(f"   · {who} ran out of time — settling the pick for them")
        # Cross the deadline on the simulated clock first. advance_if_expired
        # compares pick_expires_at against the clock, and the pick was armed at
        # now + deadline, so without this it sees nothing to do and the loop
        # waits again for a human who has already walked away.
        self.clock.advance(self.cfg.pick_deadline + 5)  # type: ignore[union-attr]
        outcome = await self.draft.advance_if_expired(
            league.id,
            getattr(self, "grid_size", 22),
            self.cfg.pick_deadline,
            display_names=names,
        )
        if outcome is not None:
            self.report.auto_picks += 1
        return outcome is not None

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
