"""Caching wrapper around any F1 provider.

Jolpica is a volunteer-run fork with per-channel rate limits. The scorer
fetches one calendar per season and one result per round, then repeats that
for every league on the same season. Without a cache a 10-league database
makes 10x the upstream calls for identical payloads, which is how a healthy
provider starts returning 429s during the post-race scoring window.

This wrapper is deliberately boring: in-memory TTL keyed on the exact
provider arguments. No disk, no cross-process sharing, no invalidation
protocol. A cron process lives for one run, so a process-local cache is
enough to collapse duplicate calls within that run. Failures are never
cached — a transient 500 must not poison the next league in the loop.
"""

from __future__ import annotations

import time
from collections.abc import Hashable
from dataclasses import dataclass

from app.provider.base import Driver, EventResult, F1Provider, Kind, Race


@dataclass(slots=True)
class _Entry[V]:
    expires_at: float
    value: V


class _TtlCache[K: Hashable, V]:
    """Minimal TTL map. Evicts lazily on read to avoid a sweeper task."""

    def __init__(self, ttl_seconds: float, *, clock=time.monotonic) -> None:
        self._ttl = max(0.0, ttl_seconds)
        self._clock = clock
        self._entries: dict[K, _Entry[V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._entries[key]
            return None
        return entry.value

    def put(self, key: K, value: V) -> None:
        self._entries[key] = _Entry(expires_at=self._clock() + self._ttl, value=value)

    def __len__(self) -> int:
        return len(self._entries)


class CachedProvider:
    """Collapse duplicate upstream calls within a single process run.

    Wraps any ``F1Provider``. Satisfies the same three-method surface so it
    can be passed anywhere a provider is expected.
    """

    def __init__(self, inner: F1Provider, *, ttl_seconds: float = 3600.0) -> None:
        self._inner = inner
        self._calendars: _TtlCache[int, list[Race]] = _TtlCache(ttl_seconds)
        self._drivers: _TtlCache[int, list[Driver]] = _TtlCache(ttl_seconds)
        self._results: _TtlCache[tuple[int, int, Kind], EventResult] = _TtlCache(ttl_seconds)

    async def calendar(self, season: int) -> list[Race]:
        cached = self._calendars.get(season)
        if cached is not None:
            return cached
        result = await self._inner.calendar(season)
        self._calendars.put(season, result)
        return result

    async def drivers(self, season: int) -> list[Driver]:
        cached = self._drivers.get(season)
        if cached is not None:
            return cached
        result = await self._inner.drivers(season)
        self._drivers.put(season, result)
        return result

    async def event_result(self, season: int, round: int, kind: Kind) -> EventResult:
        key = (season, round, kind)
        cached = self._results.get(key)
        if cached is not None:
            return cached
        result = await self._inner.event_result(season, round, kind)
        self._results.put(key, result)
        return result

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            await close()

    async def __aenter__(self) -> CachedProvider:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
