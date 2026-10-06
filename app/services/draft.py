"""Draft turn machine.

The design constraint this module exists to satisfy: a pick expires after N
minutes, but Railway cron runs at most once an hour. A cron-driven timer
therefore cannot enforce a 10-minute pick window.

The answer is that nothing is scheduled. ``draft.pick_expires_at`` is a
timestamp, and expiry is a comparison evaluated lazily from whatever request
touches the draft next — the draft page, the pick endpoint, or the hourly cron
as a backstop.

Consequences worth stating:

* Deploys are irrelevant. A timestamp in Postgres does not care about restarts.
* Double-firing is impossible. There is no task to fire twice.
* A late Discord ping is acceptable. State is always correct; only the
  notification can lag. Correctness is never traded for a notification.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.clock import Clock, SystemClock
from app.domain import draft as domain_draft

log = logging.getLogger(__name__)


class DraftRepository(Protocol):
    """Persistence surface the turn machine needs.

    Deliberately narrow: five operations. Anything the draft service needs that
    is not here belongs in a different module.
    """

    async def get_state(self, league_id: UUID) -> domain_draft.DraftState | None: ...

    async def get_team_ids(self, league_id: UUID) -> list[UUID]: ...

    async def get_taken_driver_ids(self, league_id: UUID) -> set[UUID]: ...

    async def get_available_drivers(
        self, league_id: UUID, season_year: int
    ) -> list[tuple[UUID, str, str]]: ...

    async def commit_pick(
        self,
        league_id: UUID,
        team_id: UUID,
        driver_id: UUID,
        pick_number: int,
        auto: bool,
    ) -> None: ...

    async def save_state(self, league_id: UUID, state: domain_draft.DraftState) -> None: ...

    async def set_expiry(self, league_id: UUID, expires_at: datetime) -> None: ...


class DiscordPublisher(Protocol):
    """What the turn machine says when something happens."""

    async def signup_updated(self, league_id: UUID, players: int, size: int) -> None: ...

    async def turn_started(
        self, league_id: UUID, team_id: UUID, display_name: str, deadline_secs: int
    ) -> None: ...

    async def pick_recorded(
        self,
        league_id: UUID,
        team_id: UUID,
        display_name: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None: ...

    async def draft_complete(self, league_id: UUID, rosters: dict[str, list[str]]) -> None: ...


class NoopPublisher:
    """Used in tests and when a league has no webhook configured yet."""

    async def signup_updated(self, league_id: UUID, players: int, size: int) -> None: ...

    async def turn_started(
        self, league_id: UUID, team_id: UUID, display_name: str, deadline_secs: int
    ) -> None: ...

    async def pick_recorded(
        self,
        league_id: UUID,
        team_id: UUID,
        display_name: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None: ...

    async def draft_complete(self, league_id: UUID, rosters: dict[str, list[str]]) -> None: ...


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """What happened when the draft was advanced."""

    advanced: bool
    state: domain_draft.DraftState
    auto_picked: bool = False
    driver_id: UUID | None = None


class DraftService:
    def __init__(
        self,
        repo: DraftRepository,
        publisher: DiscordPublisher | None = None,
        *,
        rng: random.Random | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repo = repo
        self.publisher = publisher or NoopPublisher()
        self.rng = rng or random.Random()
        # Injected rather than read from the module, so a simulation can move
        # virtual time and actually exercise the timeout path.
        self.clock = clock or SystemClock()

    # ── Signup ───────────────────────────────────────────────────────────────

    async def refresh_signup(
        self, league_id: UUID, season_drivers: int, size_override: int | None
    ) -> None:
        """Recompute and repost the signup summary after a join.

        The Discord message is edited in place, so the channel shows a live
        count rather than a wall of duplicates.
        """
        team_ids = await self.repo.get_team_ids(league_id)
        players = len(team_ids)
        size = domain_draft.team_size(players, season_drivers, size_override)
        await self.publisher.signup_updated(league_id, players, size)

    # ── Start ────────────────────────────────────────────────────────────────

    async def start(
        self,
        league_id: UUID,
        season_drivers: int,
        size_override: int | None,
        deadline_secs: int,
    ) -> domain_draft.DraftState:
        """Begin the draft and arm the first pick timer."""
        team_ids = await self.repo.get_team_ids(league_id)
        state = domain_draft.begin(team_ids, season_drivers, size_override, self.rng)

        await self.repo.save_state(league_id, state)
        await self._arm(league_id, state, deadline_secs)
        await self._announce_turn(league_id, state, deadline_secs)
        return state

    # ── Lazy expiry ──────────────────────────────────────────────────────────

    async def advance_if_expired(
        self,
        league_id: UUID,
        season_drivers: int,
        deadline_secs: int,
        *,
        display_names: dict[UUID, str] | None = None,
    ) -> TurnOutcome | None:
        """Roll the draft forward if the pick window has closed.

        Idempotent and safe to call from any request path. Returns None when
        nothing was due, so callers can stay quiet.
        """
        state = await self.repo.get_state(league_id)
        if state is None or state.status is not domain_draft.DraftStatus.DRAFTING:
            return None

        expires_at = await self._read_expiry(league_id)
        if expires_at is None or expires_at > self.clock.now():
            return None

        on_clock = state.on_the_clock
        if on_clock is None:
            return None

        taken = await self.repo.get_taken_driver_ids(league_id)
        pool = [d for d in await self.repo.get_available_drivers(league_id) if d[0] not in taken]
        if not pool:
            log.error("draft %s: no drivers left but picks remain", league_id)
            return None

        driver_id, driver_name, _ = self.rng.choice(pool)
        return await self._apply(
            league_id,
            state,
            on_clock,
            driver_id,
            driver_name,
            deadline_secs,
            auto=True,
            display_names=display_names,
        )

    # ── Pick ─────────────────────────────────────────────────────────────────

    async def pick(
        self,
        league_id: UUID,
        team_id: UUID,
        driver_id: UUID,
        deadline_secs: int,
        *,
        display_names: dict[UUID, str] | None = None,
    ) -> TurnOutcome:
        """Record a player pick. Raises ``ValueError`` if it isn't their turn."""
        state = await self.repo.get_state(league_id)
        if state is None:
            raise ValueError("Draft not found")

        if state.status is not domain_draft.DraftStatus.DRAFTING:
            raise ValueError("Draft is not running")

        if state.on_the_clock != team_id:
            raise ValueError("It is not your turn")

        taken = await self.repo.get_taken_driver_ids(league_id)
        if driver_id in taken:
            raise ValueError("That driver has already been picked")

        driver_name = next(
            (n for d, n, _ in await self.repo.get_available_drivers(league_id) if d == driver_id),
            "Unknown",
        )

        return await self._apply(
            league_id,
            state,
            team_id,
            driver_id,
            driver_name,
            deadline_secs,
            auto=False,
            display_names=display_names,
        )

    # ── Internals ────────────────────────────────────────────────────────────

    async def _apply(
        self,
        league_id: UUID,
        state: domain_draft.DraftState,
        team_id: UUID,
        driver_id: UUID,
        driver_name: str,
        deadline_secs: int,
        *,
        auto: bool,
        display_names: dict[UUID, str] | None,
    ) -> TurnOutcome:
        """Shared pick path. domain.advance raises on an illegal pick."""
        next_state = domain_draft.advance(state, team_id)

        await self.repo.commit_pick(league_id, team_id, driver_id, state.current_pick, auto)
        await self.repo.save_state(league_id, next_state)

        who = (display_names or {}).get(team_id, "A player")
        remaining = len(state.pick_order) - state.current_pick - 1

        if next_state.status is domain_draft.DraftStatus.COMPLETE:
            await self._announce_complete(league_id, display_names)
            return TurnOutcome(True, next_state, auto, driver_id)

        await self._arm(league_id, next_state, deadline_secs)
        await self.publisher.pick_recorded(league_id, team_id, who, driver_name, remaining, auto)
        await self._announce_turn(league_id, next_state, deadline_secs)
        return TurnOutcome(True, next_state, auto, driver_id)

    async def _arm(self, league_id: UUID, state: domain_draft.DraftState, deadline: int) -> None:
        await self.repo.set_expiry(league_id, self.clock.now() + timedelta(seconds=deadline))

    async def _announce_turn(
        self, league_id: UUID, state: domain_draft.DraftState, deadline: int
    ) -> None:
        team_id = state.on_the_clock
        if team_id is None:
            return
        await self.publisher.turn_started(league_id, team_id, str(team_id)[:8], deadline)

    async def _announce_complete(
        self, league_id: UUID, display_names: dict[UUID, str] | None
    ) -> None:
        await self.publisher.draft_complete(league_id, {})

    async def _read_expiry(self, league_id: UUID) -> datetime | None:
        getter = getattr(self.repo, "get_expiry", None)
        if getter is None:
            return None
        return await getter(league_id)
