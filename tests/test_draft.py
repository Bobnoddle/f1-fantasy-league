"""Draft mechanics tests.

No clock, no database. Every transition is a pure function of the draft state,
which is what makes the tricky parts (snake order, turn enforcement, timeout
rollover) checkable in isolation.
"""

from __future__ import annotations

import random

import pytest

from app.domain.draft import (
    AvailableView,
    DraftState,
    DraftStatus,
    advance,
    begin,
    progress,
    rounds_description,
    shuffled_order,
    snake_order,
    team_size,
)

# ── Team sizing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("players", "drivers", "expected"),
    [
        (2, 22, 10),
        (4, 22, 5),
        (5, 22, 4),
        (11, 22, 2),
        (22, 22, 1),
        (3, 22, 7),
    ],
)
def test_team_size_autoscales(players: int, drivers: int, expected: int) -> None:
    assert team_size(players, drivers) == expected


def test_team_size_never_leaves_drivers_unpicked() -> None:
    """An odd player count must not produce unpicked drivers."""
    for players in range(2, 23):
        for drivers in (20, 21, 22, 24):
            size = team_size(players, drivers)
            assert size * players <= drivers, (
                f"{players} players x {size} exceeds {drivers} available drivers"
            )


def test_team_size_override_wins() -> None:
    assert team_size(4, 22, override=8) == 8


def test_team_size_override_capped_by_driver_pool() -> None:
    assert team_size(4, 22, override=99) == 22


def test_team_size_zero_players() -> None:
    assert team_size(0, 22) == 0


# ── Snake order ─────────────────────────────────────────────────────────────


def test_snake_order_reverses_each_round() -> None:
    order = snake_order([1, 2, 3], rounds=3)
    assert order == (1, 2, 3, 3, 2, 1, 1, 2, 3)


def test_snake_order_covers_every_team_per_round() -> None:
    order = snake_order([10, 20, 30, 40], rounds=5)
    assert len(order) == 20
    for r in range(5):
        assert sorted(order[r * 4 : (r + 1) * 4]) == [10, 20, 30, 40]


def test_snake_order_single_player() -> None:
    assert snake_order([7], rounds=3) == (7, 7, 7)


# ── Shuffling ───────────────────────────────────────────────────────────────


def test_shuffle_is_deterministic_with_seed() -> None:
    a = shuffled_order([1, 2, 3, 4, 5], random.Random(42))
    b = shuffled_order([1, 2, 3, 4, 5], random.Random(42))
    assert a == b


def test_shuffle_preserves_membership() -> None:
    assert sorted(shuffled_order([1, 2, 3, 4, 5])) == [1, 2, 3, 4, 5]


# ── Beginning a draft ───────────────────────────────────────────────────────


def test_begin_builds_drafting_state() -> None:
    state = begin([1, 2, 3], season_drivers=22, rng=random.Random(1))

    assert state.status is DraftStatus.DRAFTING
    assert state.current_pick == 0
    assert state.team_size == 7
    assert len(state.pick_order) == 21
    assert state.on_the_clock in (1, 2, 3)


def test_begin_requires_two_players() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        begin([1], season_drivers=22)


def test_begin_uses_override_size() -> None:
    state = begin([1, 2], season_drivers=22, size_override=5, rng=random.Random(1))
    assert state.team_size == 5
    assert len(state.pick_order) == 10


# ── Turn enforcement ────────────────────────────────────────────────────────


def test_advance_rejects_out_of_turn_pick() -> None:
    state = begin([1, 2, 3], season_drivers=22, rng=random.Random(1))
    wrong = next(t for t in state.team_ids if t != state.on_the_clock)

    with pytest.raises(ValueError, match="not team"):
        advance(state, wrong)


def test_advance_accepts_correct_turn() -> None:
    state = begin([1, 2, 3], season_drivers=22, rng=random.Random(1))
    nxt = advance(state, state.on_the_clock)

    assert nxt.current_pick == 1
    assert nxt.status is DraftStatus.DRAFTING


def test_full_draft_completes_on_last_pick() -> None:
    state = DraftState(
        status=DraftStatus.DRAFTING,
        current_pick=1,
        pick_order=(1, 2),
        team_ids=(1, 2),
        season_drivers=22,
        team_size=1,
    )
    final = advance(state, 2)

    assert final.status is DraftStatus.COMPLETE
    assert final.on_the_clock is None


def test_advance_rejects_when_not_drafting() -> None:
    state = DraftState(
        status=DraftStatus.COMPLETE,
        current_pick=2,
        pick_order=(1, 2),
        team_ids=(1, 2),
        season_drivers=22,
        team_size=1,
    )
    with pytest.raises(ValueError, match="not running"):
        advance(state, 1)


def test_advance_preserves_pick_order() -> None:
    state = begin([1, 2], season_drivers=22, rng=random.Random(7))
    nxt = advance(state, state.on_the_clock)
    assert nxt.pick_order == state.pick_order


# ── Derived display state ───────────────────────────────────────────────────


def test_round_and_pick_in_round() -> None:
    state = DraftState(
        status=DraftStatus.DRAFTING,
        current_pick=5,
        pick_order=tuple([1, 2, 3] * 3),
        team_ids=(1, 2, 3),
        season_drivers=22,
        team_size=3,
    )
    assert state.round_number == 2  # 5 // 3 + 1
    assert state.pick_in_round == 3  # 5 % 3 + 1


def test_on_the_clock_none_when_pending() -> None:
    state = DraftState(
        status=DraftStatus.PENDING,
        current_pick=0,
        pick_order=(1, 2),
        team_ids=(1, 2),
        season_drivers=22,
        team_size=1,
    )
    assert state.on_the_clock is None


def test_on_the_clock_none_when_cursor_exhausted() -> None:
    state = DraftState(
        status=DraftStatus.DRAFTING,
        current_pick=2,
        pick_order=(1, 2),
        team_ids=(1, 2),
        season_drivers=22,
        team_size=1,
    )
    assert state.on_the_clock is None


def test_progress_reads_naturally() -> None:
    state = DraftState(
        status=DraftStatus.DRAFTING,
        current_pick=4,
        pick_order=tuple([1, 2, 3, 4] * 3),
        team_ids=(1, 2, 3, 4),
        season_drivers=22,
        team_size=3,
    )
    assert progress(state) == "Round 2 of 3 — pick 1"


def test_rounds_description_marks_reversed() -> None:
    lines = rounds_description([1, 2], rounds=2)
    assert "Round 1:" in lines[0]
    assert "(reversed)" in lines[1]


# ── Available view ──────────────────────────────────────────────────────────


def test_available_view_filters_taken_drivers() -> None:
    drivers = [(1, "Norris", "McLaren"), (2, "Piastri", "McLaren"), (3, "Leclerc", "Ferrari")]
    view = AvailableView.build(drivers, taken={2})

    assert view.total == 2
    assert "Piastri" not in str(view.by_constructor["McLaren"])


def test_available_view_groups_by_constructor() -> None:
    drivers = [(1, "Norris", "McLaren"), (2, "Piastri", "McLaren"), (3, "Leclerc", "Ferrari")]
    view = AvailableView.build(drivers, taken=set())

    assert set(view.by_constructor) == {"McLaren", "Ferrari"}
    assert len(view.by_constructor["McLaren"]) == 2


def test_available_view_empty_when_all_taken() -> None:
    drivers = [(1, "Norris", "McLaren")]
    assert AvailableView.build(drivers, taken={1}).total == 0
