"""Clock and notifier tests.

The clock matters because deadlines are evaluated by comparison: if anything can
silently substitute wall time for it, the timeout path stops being testable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.clock import SimulatedClock, SystemClock
from app.notifier import (
    ConsoleNotifier,
    FanoutNotifier,
    LeagueContext,
    NullNotifier,
)

LEAGUE = LeagueContext(id=uuid4(), code="t", name="Test", season_year=2026)


# ── Clock ───────────────────────────────────────────────────────────────────


def test_system_clock_returns_utc_now() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert abs((now - datetime.now(UTC)).total_seconds()) < 5


def test_simulated_clock_starts_near_real_time() -> None:
    clock = SimulatedClock(speed=1.0)
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5


def test_simulated_clock_can_start_in_the_past() -> None:
    start = datetime.now(UTC) - timedelta(days=365)
    assert abs((SimulatedClock(start).now() - start).total_seconds()) < 5


def test_advance_moves_forward() -> None:
    clock = SimulatedClock(speed=1.0)
    before = clock.now()
    after = clock.advance(3600)
    assert after > before
    assert abs((after - before).total_seconds() - 3600) < 2


def test_advance_returns_new_time() -> None:
    clock = SimulatedClock(speed=1.0)
    assert clock.advance(60) >= clock.now() - timedelta(seconds=1)


def test_speed_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SimulatedClock(speed=0)


def test_speed_compresses_elapsed_time() -> None:
    """A fast clock must outrun a slow one over the same real interval."""
    import time

    anchor = datetime.now(UTC) - timedelta(days=1)
    slow = SimulatedClock(anchor, speed=1.0)
    fast = SimulatedClock(anchor, speed=1000.0)

    time.sleep(0.05)

    slow_elapsed = (slow.now() - anchor).total_seconds()
    fast_elapsed = (fast.now() - anchor).total_seconds()

    assert slow_elapsed < 5  # ~0.05s of real time
    assert fast_elapsed > 10  # ~50s of virtual time
    assert fast_elapsed > slow_elapsed * 10


def test_naive_start_is_treated_as_utc() -> None:
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    assert SimulatedClock(naive).now().tzinfo is not None


# ── Notifiers ───────────────────────────────────────────────────────────────


async def test_context_reports_configuration() -> None:
    assert LeagueContext(id=uuid4(), code="x", name="n", season_year=2026).configured is False
    configured = LeagueContext(
        id=uuid4(),
        code="x",
        name="n",
        season_year=2026,
        webhook_id="1",
        webhook_token="t",
    )
    assert configured.configured is True


async def test_null_notifier_swallows_everything() -> None:
    notifier = NullNotifier()
    await notifier.signup_updated(LEAGUE, 3, 5)
    await notifier.turn_started(LEAGUE, uuid4(), "<@1>", 600)
    await notifier.pick_recorded(LEAGUE, uuid4(), "a", "Norris", 2, False)
    await notifier.draft_complete(LEAGUE, {"a": ["Norris"]})
    await notifier.results_posted(LEAGUE, "Monaco", "race", [("a", 10.0)])
    await notifier.test()


async def test_console_notifier_renders() -> None:
    notifier = ConsoleNotifier()
    await notifier.signup_updated(LEAGUE, 4, 5)
    await notifier.turn_started(LEAGUE, uuid4(), "<@1>", 600)
    await notifier.pick_recorded(LEAGUE, uuid4(), "Dave", "Norris", 2, True)
    await notifier.results_posted(LEAGUE, "Monaco", "race", [("Dave", 42.0)])


async def test_console_notifier_can_be_quiet() -> None:
    notifier = ConsoleNotifier(verbose=False)
    await notifier.signup_updated(LEAGUE, 1, 10)  # must not print


class Broken:
    async def signup_updated(self, *a):
        raise RuntimeError("channel is down")

    async def turn_started(self, *a):
        raise RuntimeError("channel is down")

    async def pick_recorded(self, *a):
        raise RuntimeError("channel is down")

    async def draft_complete(self, *a):
        raise RuntimeError("channel is down")

    async def results_posted(self, *a):
        raise RuntimeError("channel is down")

    async def test(self):
        raise RuntimeError("channel is down")


async def test_fanout_survives_a_broken_channel() -> None:
    """A dead webhook must never make a league unplayable."""
    fanout = FanoutNotifier(Broken(), ConsoleNotifier(verbose=False))
    await fanout.signup_updated(LEAGUE, 2, 8)
    await fanout.turn_started(LEAGUE, uuid4(), None, 600)
    await fanout.results_posted(LEAGUE, "Monaco", "race", [("a", 1.0)])


async def test_fanout_ignores_none() -> None:
    FanoutNotifier(None, NullNotifier())  # type: ignore[arg-type]
