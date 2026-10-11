"""Output abstraction.

Discord is where a league is *reported*, not where it *lives*. Anything that
announces a lifecycle event goes through the ``Notifier`` protocol, so the same
league can announce into Discord, into the console, into a log, or nowhere at
all.

A league with no webhook configured gets a ``ConsoleNotifier`` in development
and ``NullNotifier`` in production. It stays fully playable either way — the
channel is a read surface, not a dependency.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

log = logging.getLogger(__name__)


class Notifier(Protocol):
    """Lifecycle announcements. Every method must tolerate failure.

    A league must never become unplayable because a webhook 404'd. State lives
    in the database; announcements are best-effort by definition.
    """

    async def signup_updated(self, league: LeagueContext, players: int, size: int) -> None: ...

    async def turn_started(
        self, league: LeagueContext, team_id: UUID, mention: str | None, deadline_secs: int
    ) -> None: ...

    async def pick_recorded(
        self,
        league: LeagueContext,
        team_id: UUID,
        actor: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None: ...

    async def draft_complete(
        self, league: LeagueContext, rosters: dict[str, list[str]]
    ) -> None: ...

    async def results_posted(
        self,
        league: LeagueContext,
        event_name: str,
        kind: str,
        standings: list[tuple[str, float]],
    ) -> None: ...

    async def test(self) -> None:
        """Verify the channel works. Raises on failure."""


class LeagueContext:
    """What a notifier needs to know about the league it is announcing for.

    Passed by value rather than importing the ORM model, so ``domain`` and
    ``services`` stay independent of the database layer.
    """

    __slots__ = ("id", "code", "name", "season_year", "webhook_id", "webhook_token")

    def __init__(
        self,
        *,
        id: UUID,
        code: str,
        name: str,
        season_year: int,
        webhook_id: str | None = None,
        webhook_token: str | None = None,
    ) -> None:
        self.id = id
        self.code = code
        self.name = name
        self.season_year = season_year
        self.webhook_id = webhook_id
        self.webhook_token = webhook_token

    @property
    def configured(self) -> bool:
        return bool(self.webhook_id and self.webhook_token)


class NullNotifier:
    """No channel configured. League is fully playable, silently."""

    async def signup_updated(self, league: LeagueContext, players: int, size: int) -> None: ...

    async def turn_started(
        self, league: LeagueContext, team_id: UUID, mention: str | None, deadline_secs: int
    ) -> None: ...

    async def pick_recorded(
        self,
        league: LeagueContext,
        team_id: UUID,
        actor: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None: ...

    async def draft_complete(
        self, league: LeagueContext, rosters: dict[str, list[str]]
    ) -> None: ...

    async def results_posted(
        self,
        league: LeagueContext,
        event_name: str,
        kind: str,
        standings: list[tuple[str, float]],
    ) -> None: ...

    async def test(self) -> None:
        return None


class ConsoleNotifier:
    """Renders announcements as text.

    Used by the simulator and local development. If this reads sensibly, the
    league's lifecycle is legible without Discord at all — which is the point.
    """

    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose

    def _emit(self, icon: str, text: str) -> None:
        if self.verbose:
            print(f"  {icon} {text}", flush=True)

    async def signup_updated(self, league: LeagueContext, players: int, size: int) -> None:
        self._emit("📣", f"signup · {players} joined · {size} drivers each")

    async def turn_started(
        self, league: LeagueContext, team_id: UUID, mention: str | None, deadline_secs: int
    ) -> None:
        who = mention or str(team_id)[:8]
        self._emit("⏱", f"{who} on the clock ({deadline_secs // 60}m)")

    async def pick_recorded(
        self,
        league: LeagueContext,
        team_id: UUID,
        actor: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None:
        tag = "auto-picked" if auto else "picked"
        self._emit("🏁", f"{actor} {tag} {driver_name} · {remaining} left")

    async def draft_complete(self, league: LeagueContext, rosters: dict[str, list[str]]) -> None:
        self._emit("🏆", "draft complete")
        for name, drivers in rosters.items():
            self._emit("   ", f"{name}: {', '.join(drivers)}")

    async def results_posted(
        self,
        league: LeagueContext,
        event_name: str,
        kind: str,
        standings: list[tuple[str, float]],
    ) -> None:
        self._emit("📊", f"{event_name} ({kind}) scored")
        for pos, (name, points) in enumerate(standings, start=1):
            self._emit("   ", f"{pos}. {name} — {points:.0f}")

    async def test(self) -> None:
        self._emit("🔌", "console notifier ready")


class FanoutNotifier:
    """Deliver to several channels. One failing never blocks the others."""

    def __init__(self, *notifiers: Notifier) -> None:
        self._notifiers = [n for n in notifiers if n is not None]

    async def _each(self, method: str, *args, **kwargs) -> None:
        for notifier in self._notifiers:
            try:
                await getattr(notifier, method)(*args, **kwargs)
            except Exception as exc:
                # Deliberately broad. A broken channel must not break the league.
                log.warning("notifier %s.%s failed: %s", type(notifier).__name__, method, exc)

    async def signup_updated(self, league: LeagueContext, players: int, size: int) -> None:
        await self._each("signup_updated", league, players, size)

    async def turn_started(
        self, league: LeagueContext, team_id: UUID, mention: str | None, deadline_secs: int
    ) -> None:
        await self._each("turn_started", league, team_id, mention, deadline_secs)

    async def pick_recorded(
        self,
        league: LeagueContext,
        team_id: UUID,
        actor: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None:
        await self._each("pick_recorded", league, team_id, actor, driver_name, remaining, auto)

    async def draft_complete(self, league: LeagueContext, rosters: dict[str, list[str]]) -> None:
        await self._each("draft_complete", league, rosters)

    async def results_posted(
        self,
        league: LeagueContext,
        event_name: str,
        kind: str,
        standings: list[tuple[str, float]],
    ) -> None:
        await self._each("results_posted", league, event_name, kind, standings)

    async def test(self) -> None:
        await self._each("test")
