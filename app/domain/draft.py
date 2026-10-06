"""Draft mechanics. Pure state transitions — no clock, no database.

The deadline lives in the database as a timestamp and is evaluated lazily by
``advance_if_expired``. Nothing here reads the current time. That separation is
deliberate: it makes every transition in this module a pure function of
(draft state, pick), which is what makes the draft testable at all.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.constants import MAX_TEAM_SIZE


class DraftStatus(StrEnum):
    PENDING = "pending"
    SIGNUP = "signup"
    READY = "ready"
    DRAFTING = "drafting"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class DraftState:
    """Everything needed to decide what happens next."""

    status: DraftStatus
    current_pick: int
    pick_order: tuple[int, ...]
    team_ids: tuple[int, ...]
    season_drivers: int
    team_size: int

    @property
    def total_picks(self) -> int:
        return len(self.pick_order)

    @property
    def on_the_clock(self) -> int | None:
        """Team id whose turn it is, or None when the draft is not running."""
        if self.status is not DraftStatus.DRAFTING:
            return None
        if self.current_pick >= len(self.pick_order):
            return None
        return self.pick_order[self.current_pick]

    @property
    def round_number(self) -> int:
        """1-based round. Derives from player count, not stored state."""
        players = len(self.team_ids)
        if players == 0:
            return 0
        return self.current_pick // players + 1

    @property
    def pick_in_round(self) -> int:
        players = len(self.team_ids)
        return self.current_pick % players + 1 if players else 0


def team_size(players: int, season_drivers: int, override: int | None = None) -> int:
    """Drivers per team.

    Auto-scales as ``floor(drivers / players)`` capped at MAX_TEAM_SIZE so an
    odd player count never leaves drivers unpicked. An explicit override wins.
    """
    if override and override > 0:
        return min(override, season_drivers)
    if players < 1:
        return 0
    return min(MAX_TEAM_SIZE, season_drivers // players)


def snake_order(team_ids: Sequence[int], rounds: int) -> tuple[int, ...]:
    """Pick sequence: forwards, then reversed, alternating per round."""
    order: list[int] = []
    for round_index in range(rounds):
        order.extend(team_ids if round_index % 2 == 0 else reversed(team_ids))
    return tuple(order)


def shuffled_order(team_ids: Sequence[int], rng: random.Random | None = None) -> tuple[int, ...]:
    """Randomised seed order. Same seed, same order, for tests."""
    ids = list(team_ids)
    (rng or random).shuffle(ids)
    return tuple(ids)


def rounds_description(team_ids: Sequence[int], rounds: int) -> list[str]:
    """Human-readable order reveal for the admin panel."""
    names = {tid: str(tid) for tid in team_ids}
    lines: list[str] = []
    for round_index in range(rounds):
        order = team_ids if round_index % 2 == 0 else list(reversed(team_ids))
        joined = " -> ".join(names[t] for t in order)
        suffix = " (reversed)" if round_index % 2 else ""
        lines.append(f"Round {round_index + 1}: {joined}{suffix}")
    return lines


def begin(
    team_ids: Sequence[int],
    season_drivers: int,
    size_override: int | None = None,
    rng: random.Random | None = None,
) -> DraftState:
    """Build the initial drafting state from a confirmed player list."""
    if len(team_ids) < 2:
        raise ValueError("Need at least 2 players to start a draft")

    size = team_size(len(team_ids), season_drivers, size_override)
    if size < 1:
        raise ValueError("Season driver pool is too small for this player count")

    seeded = shuffled_order(team_ids, rng)
    return DraftState(
        status=DraftStatus.DRAFTING,
        current_pick=0,
        pick_order=snake_order(seeded, size),
        team_ids=seeded,
        season_drivers=season_drivers,
        team_size=size,
    )


def advance(
    state: DraftState,
    team_id: int,
) -> DraftState:
    """Advance the cursor after a validated pick.

    Deliberately takes no driver id. Roster membership is a service concern:
    this module owns turn order and the cursor, and nothing else.

    Raises rather than silently tolerating an illegal pick — a draft that
    quietly accepts an out-of-turn or out-of-roster pick produces a corrupt
    league that nobody notices until the final standings.
    """
    if state.status is not DraftStatus.DRAFTING:
        raise ValueError(f"Draft is not running (status={state.status})")

    if state.on_the_clock != team_id:
        raise ValueError(f"It is not team {team_id}'s turn")

    next_pick = state.current_pick + 1
    status = DraftStatus.COMPLETE if next_pick >= state.total_picks else DraftStatus.DRAFTING

    return DraftState(
        status=status,
        current_pick=next_pick,
        pick_order=state.pick_order,
        team_ids=state.team_ids,
        season_drivers=state.season_drivers,
        team_size=state.team_size,
    )


@dataclass(slots=True)
class AvailableView:
    """Drivers still selectable, grouped for display."""

    by_constructor: dict[str, list[dict]] = field(default_factory=dict)
    total: int = 0

    @classmethod
    def build(
        cls,
        drivers: Sequence[tuple[int, str, str]],
        taken: set[int],
    ) -> AvailableView:
        """``drivers`` is (id, name, constructor). Taken ids are filtered out."""
        view = cls()
        for driver_id, name, constructor in drivers:
            if driver_id in taken:
                continue
            view.by_constructor.setdefault(constructor, []).append({"id": driver_id, "name": name})
        for entries in view.by_constructor.values():
            entries.sort(key=lambda d: d["name"])
        view.total = sum(len(v) for v in view.by_constructor.values())
        return view


def progress(state: DraftState) -> str:
    """One-line draft progress, e.g. 'Round 3 of 5 — pick 2'."""
    if state.status is not DraftStatus.DRAFTING:
        return str(state.status).title()
    rounds = max(1, state.total_picks // max(1, len(state.team_ids)))
    return f"Round {state.round_number} of {rounds} — pick {state.pick_in_round}"
