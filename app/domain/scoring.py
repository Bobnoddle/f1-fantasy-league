"""Pure scoring engine. No database, no HTTP, no Discord, no config.

Every function here is deterministic and total. If it can be computed from the
inputs alone, it belongs in this module and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.constants import (
    COMPLETION_BONUS,
    DSQ_PENALTY,
    FASTEST_LAP_BONUS,
    POSITION_GAIN_BONUS,
    QUALI_POINTS,
    RACE_POINTS,
    SPRINT_DIVISOR,
)


class EventKind(StrEnum):
    RACE = "race"
    SPRINT = "sprint"


@dataclass(frozen=True, slots=True)
class Result:
    """One driver's outcome in one event, normalised.

    ``position`` is None for DNF/DSQ. ``quali`` is None for sprints.
    """

    grid: int | None
    position: int | None
    dnf: bool
    dsq: bool
    fastest_lap: bool
    quali: int | None = None

    @property
    def classified(self) -> bool:
        return not (self.dnf or self.dsq)


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Itemised points. Persisted as JSON so a score stays auditable."""

    finish: int = 0
    quali: int = 0
    completion: int = 0
    gain: int = 0
    fastest_lap: int = 0
    dsq: int = 0

    @property
    def total(self) -> float:
        return float(
            self.finish + self.quali + self.completion + self.gain + self.fastest_lap + self.dsq
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "finish": self.finish,
            "quali": self.quali,
            "completion": self.completion,
            "gain": self.gain,
            "fastest_lap": self.fastest_lap,
            "dsq": self.dsq,
            "total": self.total,
        }

    def summary(self) -> str:
        """Compact one-line rendering, e.g. '13 finish + 3 comp + 8 gain + 5 FL'."""
        parts = [
            f"{self.finish} finish",
            f"{self.quali} quali",
            f"{self.completion} comp",
            f"{self.gain} gain",
            f"{self.fastest_lap} FL",
        ]
        if self.dsq:
            parts.append(f"{self.dsq} DSQ")
        return " + ".join(p for p in parts if not p.startswith("0 "))


def position_gain(grid: int | None, position: int | None) -> int:
    """Points for places gained. Zero unless both positions are known."""
    if grid is None or position is None:
        return 0
    gained = grid - position
    return gained * POSITION_GAIN_BONUS if gained > 0 else 0


def score(result: Result, kind: EventKind = EventKind.RACE) -> Breakdown:
    """Score one driver in one event.

    Order matters: DSQ zeroes everything then applies the penalty, DNF scores
    nothing, and only a classified result earns any points at all.
    """
    if result.dsq:
        return Breakdown(dsq=DSQ_PENALTY)

    if result.dnf:
        return Breakdown()

    finish = 0
    quali = 0
    if kind is EventKind.SPRINT:
        # Finish points halved and floored. No qualifying points in a sprint.
        if result.position is not None:
            finish = RACE_POINTS.get(result.position, 0) // SPRINT_DIVISOR
    else:
        if result.position is not None:
            finish = RACE_POINTS.get(result.position, 0)
        if result.quali is not None:
            quali = QUALI_POINTS.get(result.quali, 0)

    return Breakdown(
        finish=finish,
        quali=quali,
        completion=COMPLETION_BONUS,
        gain=position_gain(result.grid, result.position),
        fastest_lap=FASTEST_LAP_BONUS if result.fastest_lap else 0,
    )


def score_team(results: list[Result], kind: EventKind = EventKind.RACE) -> float:
    """Total for a whole roster in one event."""
    return sum(score(r, kind).total for r in results)


def season_total(scores: list[float]) -> float:
    """Total across a season. Trivial, but named so the intent is explicit."""
    return float(sum(scores))


# ── Standings ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RankedRow:
    position: int
    team_id: str
    display_name: str
    points: float
    gap_to_leader: float
    trend: int  # +1 gained a place, -1 lost one, 0 unchanged


def rank(
    rows: list[tuple[str, str, float]], previous: dict[str, int] | None = None
) -> list[RankedRow]:
    """Rank teams by points descending, with gap-to-leader and movement.

    ``previous`` maps team_id to last round's position, for the trend arrow.
    Ties break on display_name so ordering is stable and reproducible.
    """
    ordered = sorted(rows, key=lambda r: (-r[2], r[1]))
    if not ordered:
        return []

    leader = ordered[0][2]
    ranked: list[RankedRow] = []
    for idx, (team_id, name, points) in enumerate(ordered, start=1):
        trend = 0
        if previous and team_id in previous:
            trend = previous[team_id] - idx
        ranked.append(
            RankedRow(
                position=idx,
                team_id=team_id,
                display_name=name,
                points=points,
                gap_to_leader=leader - points,
                trend=trend,
            )
        )
    return ranked
