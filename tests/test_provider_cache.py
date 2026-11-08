"""CachedProvider tests. No database, no network — a counting fake inner."""

from __future__ import annotations

import contextlib

from app.provider.base import Driver, DriverResult, EventResult, Kind, Race, Status
from app.provider.cached import CachedProvider


class FakeInner:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {"calendar": 0, "drivers": 0, "event_result": 0}
        self.closed = False

    async def calendar(self, season: int) -> list[Race]:
        self.calls["calendar"] += 1
        return [Race(round=1, name="GP", date="2025-03-01T00:00:00", sprint_date=None)]

    async def drivers(self, season: int) -> list[Driver]:
        self.calls["drivers"] += 1
        return [Driver(code="VER", name="Max Verstappen", constructor="Red Bull")]

    async def event_result(self, season: int, round: int, kind: Kind) -> EventResult:
        self.calls["event_result"] += 1
        return EventResult(
            season=season,
            round=round,
            kind=kind,
            name="GP",
            results=(
                DriverResult(
                    code="VER",
                    status=Status.FINISHED,
                    position=1,
                    grid=1,
                    fastest_lap=True,
                ),
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_calendar_is_cached_within_ttl() -> None:
    inner = FakeInner()
    provider = CachedProvider(inner, ttl_seconds=60.0)

    first = await provider.calendar(2025)
    second = await provider.calendar(2025)

    assert first is second
    assert inner.calls["calendar"] == 1


async def test_different_seasons_are_different_keys() -> None:
    inner = FakeInner()
    provider = CachedProvider(inner, ttl_seconds=60.0)

    await provider.calendar(2025)
    await provider.calendar(2026)

    assert inner.calls["calendar"] == 2


async def test_event_result_key_includes_round_and_kind() -> None:
    inner = FakeInner()
    provider = CachedProvider(inner, ttl_seconds=60.0)

    await provider.event_result(2025, 1, Kind.RACE)
    await provider.event_result(2025, 1, Kind.RACE)
    await provider.event_result(2025, 2, Kind.RACE)
    await provider.event_result(2025, 1, Kind.SPRINT)

    assert inner.calls["event_result"] == 3


async def test_expired_entry_refetches() -> None:
    now = [100.0]
    from app.provider.cached import _TtlCache

    cache: _TtlCache[str, str] = _TtlCache(10.0, clock=lambda: now[0])
    cache.put("k", "v")
    assert cache.get("k") == "v"
    now[0] = 111.0
    assert cache.get("k") is None


async def test_failures_are_not_cached() -> None:
    class Flaky(FakeInner):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def calendar(self, season: int):  # type: ignore[override]
            self.calls["calendar"] += 1
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("boom")
            return await super().calendar(season)

    inner = Flaky()
    provider = CachedProvider(inner, ttl_seconds=60.0)

    with contextlib.suppress(RuntimeError):
        await provider.calendar(2025)
    await provider.calendar(2025)
    await provider.calendar(2025)

    # 1 failure + 1 success + 0 (cached) + the success inside retry counts once
    assert inner.calls["calendar"] == 3


async def test_aclose_forwards_to_inner() -> None:
    inner = FakeInner()
    provider = CachedProvider(inner)
    await provider.aclose()
    assert inner.closed is True
