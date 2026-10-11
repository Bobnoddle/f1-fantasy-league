"""Clock abstraction.

Draft deadlines are evaluated by comparison against the current time. That is
correct and deploy-proof, but it also means time travel is impossible unless the
clock is injectable — and without time travel you cannot replay a finished
season in seconds, which is the main thing the simulator is for.

Injected everywhere rather than read from the module, so a test or a simulation
controls expiry, race windows, and standings history consistently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time. The only implementation used in production."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulatedClock:
    """Virtual time.

    ``speed`` compresses elapsed time: ``speed=3600`` means one real second
    passes as one virtual hour, so a 24-round season replays in under a minute.
    ``advance`` jumps forward explicitly, which is what a draft timeout wants —
    waiting 600 virtual seconds should not take 600 real ones.
    """

    __slots__ = ("_start", "_offset", "_speed", "_anchor")

    def __init__(
        self,
        start: datetime | None = None,
        *,
        speed: float = 1.0,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._speed = speed
        self._anchor = datetime.now(UTC)
        self._start = start or self._anchor
        if self._start.tzinfo is None:
            self._start = self._start.replace(tzinfo=UTC)
        # Offset is only ever an explicit advance(). Compensating for `_start`
        # here as well would apply it twice, putting a clock created one day in
        # the past two days in the past.
        self._offset = timedelta(0)

    def now(self) -> datetime:
        elapsed = (datetime.now(UTC) - self._anchor).total_seconds()
        return self._start + timedelta(seconds=elapsed * self._speed + self._offset.total_seconds())

    def advance(self, seconds: float) -> datetime:
        """Jump forward. Returns the new virtual time."""
        self._offset += timedelta(seconds=seconds)
        return self.now()


def utcnow() -> datetime:
    return SystemClock().now()
