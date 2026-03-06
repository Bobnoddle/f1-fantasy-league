"""
utils/scoring.py — Pure-function scoring engine for F1 Fantasy.

Scoring summary
---------------
Race finish:     P1=20 … P20=2 (all positions score)
Qualifying:      P1=8 … P15=1, P16-P20=0
Completion:      +3 if driver finished (not DNF, not DSQ)
Position gain:   +4 per place gained (grid_pos - finish_pos, positive only)
Fastest lap:     +5
DSQ:             -15 (all other components zeroed)
DNF:             0 points (no finish, no completion, no gain)
Sprint:          finish points at ½ value (floor); all bonuses at full value;
                 no qualifying points
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import (
    RACE_POINTS,
    QUALI_POINTS,
    COMPLETION_BONUS,
    POSITION_GAIN_BONUS,
    FASTEST_LAP_BONUS,
    DSQ_PENALTY,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DriverResult:
    """Raw result data for one driver in one event."""
    driver_id: int
    grid_position: Optional[int]    # None if unknown / pit-lane start
    finish_position: Optional[int]  # None if DNF or DSQ
    dnf: bool
    dsq: bool
    fastest_lap: bool
    quali_position: Optional[int]   # None for sprint events


@dataclass
class ScoreBreakdown:
    """Detailed point breakdown for one driver in one event."""
    finish_pts: int = 0
    quali_pts: int = 0
    completion_pts: int = 0
    gain_pts: int = 0
    fastest_lap_pts: int = 0
    dsq_pts: int = 0
    total: float = 0.0

    def as_dict(self) -> dict:
        return {
            "finish": self.finish_pts,
            "quali": self.quali_pts,
            "completion": self.completion_pts,
            "gain": self.gain_pts,
            "fastest_lap": self.fastest_lap_pts,
            "dsq": self.dsq_pts,
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _gain_pts(grid_position: Optional[int], finish_position: Optional[int]) -> int:
    """Return position-gain bonus points. Returns 0 if either position is unknown."""
    if grid_position is None or finish_position is None:
        return 0
    places_gained = grid_position - finish_position
    return max(0, places_gained) * POSITION_GAIN_BONUS


def _total(bd: ScoreBreakdown) -> float:
    return float(
        bd.finish_pts
        + bd.quali_pts
        + bd.completion_pts
        + bd.gain_pts
        + bd.fastest_lap_pts
        + bd.dsq_pts
    )


# ---------------------------------------------------------------------------
# Public scoring functions
# ---------------------------------------------------------------------------

def score_race(result: DriverResult) -> ScoreBreakdown:
    """Score a standard (full) race result for one driver.

    Parameters
    ----------
    result:
        Populated :class:`DriverResult` for the driver.

    Returns
    -------
    ScoreBreakdown
        Fully populated breakdown with ``total`` set.
    """
    bd = ScoreBreakdown()

    if result.dsq:
        bd.dsq_pts = DSQ_PENALTY
        bd.total = float(DSQ_PENALTY)
        return bd

    if result.dnf:
        # All components remain 0
        return bd

    # Finished — award points
    bd.finish_pts = RACE_POINTS.get(result.finish_position, 0) if result.finish_position is not None else 0
    bd.completion_pts = COMPLETION_BONUS
    bd.gain_pts = _gain_pts(result.grid_position, result.finish_position)
    bd.fastest_lap_pts = FASTEST_LAP_BONUS if result.fastest_lap else 0
    bd.quali_pts = (
        QUALI_POINTS.get(result.quali_position, 0)
        if result.quali_position is not None
        else 0
    )
    bd.total = _total(bd)
    return bd


def score_sprint(result: DriverResult) -> ScoreBreakdown:
    """Score a sprint race result for one driver.

    Sprint rules differ from a standard race:
    - Finish points are halved (integer floor division).
    - No qualifying points are awarded.
    - Completion, position-gain, and fastest-lap bonuses apply at full value.
    - DSQ / DNF handling is identical to a standard race.
    """
    bd = ScoreBreakdown()

    if result.dsq:
        bd.dsq_pts = DSQ_PENALTY
        bd.total = float(DSQ_PENALTY)
        return bd

    if result.dnf:
        return bd

    # Finished — half finish points, full bonuses
    bd.finish_pts = (
        RACE_POINTS.get(result.finish_position, 0) // 2
        if result.finish_position is not None
        else 0
    )
    # quali_pts intentionally left at 0 for sprint events
    bd.completion_pts = COMPLETION_BONUS
    bd.gain_pts = _gain_pts(result.grid_position, result.finish_position)
    bd.fastest_lap_pts = FASTEST_LAP_BONUS if result.fastest_lap else 0
    bd.total = _total(bd)
    return bd


def score_qualifying(quali_position: Optional[int]) -> ScoreBreakdown:
    """Score a standalone qualifying session.

    Parameters
    ----------
    quali_position:
        The driver's classified qualifying position (1-based), or ``None``
        if the driver did not set a time / was excluded.
    """
    bd = ScoreBreakdown()
    bd.quali_pts = QUALI_POINTS.get(quali_position, 0) if quali_position is not None else 0
    bd.total = float(bd.quali_pts)
    return bd


def score_event(result: DriverResult, *, is_sprint: bool = False) -> ScoreBreakdown:
    """Convenience dispatcher: route to :func:`score_sprint` or :func:`score_race`.

    Parameters
    ----------
    result:
        Populated :class:`DriverResult` for the driver.
    is_sprint:
        ``True`` for a sprint race; ``False`` (default) for a full race.
    """
    if is_sprint:
        return score_sprint(result)
    return score_race(result)
