"""Simulator agent behaviour.

The simulator's job is to hit the awkward paths on purpose: players who blow
their deadlines, players who concentrate on one constructor, and the double-pick
that the roster primary key must reject.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.sim.agents import (
    DriverView,
    Pace,
    SimAgent,
    Strategy,
    default_roster,
)


def pool(constructors: list[str]) -> list[DriverView]:
    return [
        DriverView(id=uuid4(), name=f"{c} driver {i}", constructor=c)
        for i, c in enumerate(constructors)
    ]


# ── Pace ────────────────────────────────────────────────────────────────────


def test_instant_agents_never_time_out() -> None:
    agent = SimAgent("fast", pace=Pace.INSTANT, seed=1)
    assert not any(agent.will_timeout(600, agent.rng) for _ in range(200))


def test_slow_agents_mostly_time_out() -> None:
    agent = SimAgent("slow", pace=Pace.SLOW, seed=1)
    timeouts = sum(agent.will_timeout(600, agent.rng) for _ in range(300))
    assert timeouts / 300 > 0.5


def test_flaky_sits_between() -> None:
    agent = SimAgent("flaky", pace=Pace.FLAKY, seed=1)
    rate = sum(agent.will_timeout(600, agent.rng) for _ in range(400)) / 400
    assert 0.05 < rate < 0.55


def test_delay_never_exceeds_deadline() -> None:
    """A non-timeout agent must land inside the window, or the pick is late."""
    for pace in Pace:
        agent = SimAgent("a", pace=pace, seed=3)
        for _ in range(100):
            if pace in (Pace.INSTANT, Pace.NORMAL):
                assert agent.delay(600, agent.rng) < 600


def test_delay_is_zero_for_instant() -> None:
    agent = SimAgent("a", pace=Pace.INSTANT, seed=3)
    assert all(agent.delay(600, agent.rng) == 0 for _ in range(20))


# ── Determinism ─────────────────────────────────────────────────────────────


def test_same_seed_same_behaviour() -> None:
    a = SimAgent("x", pace=Pace.SLOW, seed=11)
    b = SimAgent("x", pace=Pace.SLOW, seed=11)
    assert [a.will_timeout(300, a.rng) for _ in range(20)] == [
        b.will_timeout(300, b.rng) for _ in range(20)
    ]


def test_different_agents_do_not_share_a_stream() -> None:
    """Turn order must not change who times out."""
    a = SimAgent("alice", pace=Pace.SLOW, seed=11)
    b = SimAgent("bob", pace=Pace.SLOW, seed=11)
    assert [a.will_timeout(300, a.rng) for _ in range(20)] != [
        b.will_timeout(300, b.rng) for _ in range(20)
    ]


# ── Strategy ────────────────────────────────────────────────────────────────


def test_random_chooses_from_the_pool() -> None:
    agent = SimAgent("a", strategy=Strategy.RANDOM, seed=1)
    drivers = pool(["McLaren"] * 5)
    assert agent.choose(drivers, {}, agent.rng) in drivers


def test_spread_prefers_an_untouched_constructor() -> None:
    agent = SimAgent("a", strategy=Strategy.SPREAD, seed=1)
    drivers = pool(["McLaren", "Ferrari", "Red Bull"])

    for _ in range(30):
        choice = agent.choose(drivers, {"me": ["McLaren"]}, agent.rng)
        assert choice.constructor != "McLaren"


def test_best_team_concentrates() -> None:
    agent = SimAgent("a", strategy=Strategy.BEST_TEAM, seed=1)
    drivers = pool(["McLaren", "Ferrari", "Red Bull"])

    for _ in range(30):
        assert agent.choose(drivers, {"me": ["Ferrari"]}, agent.rng).constructor == "Ferrari"


def test_conservative_takes_the_deepest_pool() -> None:
    agent = SimAgent("a", strategy=Strategy.CONSERVATIVE, seed=1)
    drivers = pool(["McLaren", "McLaren", "McLaren", "Ferrari", "Red Bull"])
    for _ in range(30):
        assert agent.choose(drivers, {}, agent.rng).constructor == "McLaren"


def test_every_strategy_handles_an_empty_owned_map() -> None:
    for strategy in Strategy:
        agent = SimAgent("a", strategy=strategy, seed=1)
        assert agent.choose(pool(["McLaren", "Ferrari"]), {}, agent.rng) is not None


def test_choose_raises_on_empty_pool() -> None:
    agent = SimAgent("a", seed=1)
    with pytest.raises(ValueError, match="no drivers"):
        agent.choose([], {}, agent.rng)


# ── Rosters ─────────────────────────────────────────────────────────────────


def test_default_roster_is_deterministic() -> None:
    a = default_roster(["A", "B", "C"], seed=5)
    b = default_roster(["A", "B", "C"], seed=5)
    assert [(x.name, x.pace, x.strategy) for x in a] == [(y.name, y.pace, y.strategy) for y in b]


def test_default_roster_mixes_paces() -> None:
    agents = default_roster([f"P{i}" for i in range(6)], seed=1)
    assert len({a.pace for a in agents}) > 1


def test_default_roster_includes_a_timeout_capable_agent() -> None:
    """A roster with nobody who can time out never exercises the auto-pick."""
    agents = default_roster([f"P{i}" for i in range(6)], seed=1)
    assert any(a.pace in (Pace.SLOW, Pace.FLAKY) for a in agents)
