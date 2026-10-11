"""Scoring engine tests.

The ``Lapped`` cases are the regression guard. Against real 2024-2026 data an
earlier parser classified 76 of 300 results as DNF, suppressing ~25% of all
available points. If one of these fails, the parser has regressed.
"""

from __future__ import annotations

import pytest

from app.domain.scoring import (
    Breakdown,
    EventKind,
    Result,
    position_gain,
    rank,
    score,
    score_team,
)
from app.provider.base import Status

# ── Status classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Finished", Status.FINISHED),
        ("Lapped", Status.LAPPED),  # the regression
        ("+1 Lap", Status.LAPPED),
        ("+12 Laps", Status.LAPPED),
        ("Retired", Status.RETIRED),
        ("Did not start", Status.DID_NOT_START),
        ("Disqualified", Status.DSQ),
    ],
)
def test_parse_status(raw: str, expected: Status) -> None:
    from app.provider.base import parse_status

    assert parse_status(raw) is expected


def test_parse_status_rejects_unknown() -> None:
    from app.provider.base import parse_status

    with pytest.raises(ValueError, match="Unrecognised"):
        parse_status("Hydrated")


def test_lapped_is_classified() -> None:
    assert Status.LAPPED.classified
    assert Status.FINISHED.classified
    assert not Status.RETIRED.classified
    assert not Status.DSQ.classified


# ── Scoring ─────────────────────────────────────────────────────────────────


def test_lapped_driver_scores_as_finish() -> None:
    """A car a lap down still finished. It must not score zero."""
    result = Result(grid=3, position=6, dnf=False, dsq=False, fastest_lap=False)

    bd = score(result)

    assert bd.finish == 7  # P6
    assert bd.completion == 3
    assert bd.total == 10.0


def test_podium_finisher() -> None:
    result = Result(grid=1, position=1, dnf=False, dsq=False, fastest_lap=True, quali=1)
    bd = score(result)

    assert bd.finish == 20
    assert bd.quali == 8
    assert bd.completion == 3
    assert bd.gain == 0
    assert bd.fastest_lap == 5
    assert bd.total == 36.0


def test_dnf_scores_nothing() -> None:
    bd = score(Result(grid=2, position=None, dnf=True, dsq=False, fastest_lap=False))

    assert bd.total == 0.0
    assert bd.finish == 0
    assert bd.completion == 0


def test_dsq_zeroes_then_penalises() -> None:
    bd = score(Result(grid=1, position=1, dnf=False, dsq=True, fastest_lap=True, quali=1))

    assert bd.dsq == -15
    assert bd.finish == 0
    assert bd.quali == 0
    assert bd.fastest_lap == 0
    assert bd.total == -15.0


def test_position_gain_only_when_gained() -> None:
    assert position_gain(15, 10) == 20
    assert position_gain(5, 12) == 0  # lost places
    assert position_gain(5, 5) == 0
    assert position_gain(None, 5) == 0
    assert position_gain(5, None) == 0


def test_sprint_halves_finish_keeps_bonuses() -> None:
    result = Result(grid=3, position=2, dnf=False, dsq=False, fastest_lap=True, quali=1)
    bd = score(result, EventKind.SPRINT)

    assert bd.finish == 8  # 16 // 2
    assert bd.quali == 0  # no qualifying in a sprint
    assert bd.completion == 3  # full value
    assert bd.gain == 4  # full value
    assert bd.fastest_lap == 5
    assert bd.total == 20.0


def test_sprint_half_rounds_down() -> None:
    # P3 = 13 in a race; halved it must floor rather than round to 7.
    bd = score(
        Result(grid=3, position=3, dnf=False, dsq=False, fastest_lap=False), EventKind.SPRINT
    )
    assert bd.finish == 6


def test_all_positions_score() -> None:
    """No classified position should score zero, including P20."""
    for pos in range(1, 21):
        bd = score(Result(grid=pos, position=pos, dnf=False, dsq=False, fastest_lap=False))
        assert bd.finish > 0, f"P{pos} scored nothing"


def test_score_team_sums() -> None:
    results = [
        Result(grid=1, position=1, dnf=False, dsq=False, fastest_lap=False),
        Result(grid=10, position=20, dnf=False, dsq=False, fastest_lap=False),
        Result(grid=2, position=None, dnf=True, dsq=False, fastest_lap=False),
    ]
    assert score_team(results) == score(results[0]).total + score(results[1]).total


def test_breakdown_summary_omits_zero_components() -> None:
    summary = Breakdown(finish=20, completion=3).summary()
    assert "20 finish" in summary
    assert "quali" not in summary


def test_breakdown_roundtrips_to_dict() -> None:
    bd = score(Result(grid=15, position=10, dnf=False, dsq=False, fastest_lap=False))
    data = bd.as_dict()

    assert data["total"] == bd.total
    assert data["gain"] == 20


# ── Standings ───────────────────────────────────────────────────────────────


def test_rank_orders_by_points() -> None:
    rows = [("t1", "Dave", 100.0), ("t2", "Sam", 250.0), ("t3", "Priya", 180.0)]
    ranked = rank(rows)

    assert [r.display_name for r in ranked] == ["Sam", "Priya", "Dave"]
    assert ranked[0].gap_to_leader == 0
    assert ranked[1].gap_to_leader == 70
    assert ranked[2].gap_to_leader == 150


def test_rank_ties_break_deterministically() -> None:
    rows = [("t2", "Sam", 100.0), ("t1", "Dave", 100.0)]
    assert [r.display_name for r in rank(rows)] == ["Dave", "Sam"]


def test_rank_reports_trend_from_previous() -> None:
    rows = [("t1", "Dave", 100.0), ("t2", "Sam", 90.0)]
    ranked = rank(rows, previous={"t1": 2, "t2": 1})

    assert ranked[0].trend == 1  # Dave moved up from 2nd
    assert ranked[1].trend == -1  # Sam dropped


def test_rank_empty() -> None:
    assert rank([]) == []
