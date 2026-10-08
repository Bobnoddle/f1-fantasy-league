"""Simulated players.

Each agent has a personality: how quickly it picks, how often it blows the
deadline, and how it chooses. Together they exercise every path the real system
has — deliberate picks, timeout auto-picks, and near-deadline saves.

The point is not to produce a realistic league. It is to hit the awkward paths
reproducibly, which is what makes this worth running in CI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class Pace(StrEnum):
    INSTANT = "instant"  # picks immediately
    NORMAL = "normal"  # a few seconds, well inside the deadline
    SLOW = "slow"  # often misses the deadline
    FLAKY = "flaky"  # mostly fine, occasionally blows it


#: Fraction of picks each pace lets time out on.
_TIMEOUT_RATE: dict[Pace, float] = {
    Pace.INSTANT: 0.0,
    Pace.NORMAL: 0.0,
    Pace.FLAKY: 0.25,
    Pace.SLOW: 0.7,
}

#: Virtual seconds to burn before acting, as a fraction of the deadline.
_DELAY_RATE: dict[Pace, float] = {
    Pace.INSTANT: 0.0,
    Pace.NORMAL: 0.15,
    Pace.FLAKY: 0.45,
    Pace.SLOW: 0.85,
}


class Strategy(StrEnum):
    RANDOM = "random"  # uniform over the pool
    BEST_TEAM = "best_team"  # concentrate on the strongest constructor
    SPREAD = "spread"  # one driver per constructor first
    CONSERVATIVE = "conservative"  # prefer established, low-variance names


@dataclass(frozen=True, slots=True)
class DriverView:
    id: UUID
    name: str
    constructor: str


class Agent(Protocol):
    name: str
    pace: Pace
    strategy: Strategy

    def delay(self, deadline_secs: int, rng: random.Random) -> int:
        """Virtual seconds to wait before acting."""

    def choose(
        self,
        available: list[DriverView],
        already_owned: dict[str, list[str]],
        rng: random.Random,
    ) -> DriverView: ...


class SimAgent:
    """A simulated player."""

    def __init__(
        self,
        name: str,
        pace: Pace = Pace.NORMAL,
        strategy: Strategy = Strategy.RANDOM,
        *,
        seed: int = 0,
    ) -> None:
        self.name = name
        self.pace = pace
        self.strategy = strategy
        # Own RNG per agent. Sharing one across agents makes each agent's
        # behaviour depend on how many turns came before it, so a timeout that
        # should fire can silently not.
        self.rng = random.Random(f"{seed}:{name}")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Agent {self.name} {self.pace}/{self.strategy}>"

    def will_timeout(self, deadline_secs: int, rng: random.Random) -> bool:
        return rng.random() < _TIMEOUT_RATE[self.pace]

    def delay(self, deadline_secs: int, rng: random.Random) -> int:
        base = deadline_secs * _DELAY_RATE[self.pace]
        # Jitter so agents don't act in lockstep.
        return int(base * rng.uniform(0.7, 1.3))

    def choose(
        self,
        available: list[DriverView],
        already_owned: dict[str, list[str]],
        rng: random.Random,
    ) -> DriverView:
        if not available:
            raise ValueError("no drivers available to choose from")

        if self.strategy is Strategy.RANDOM:
            return rng.choice(available)

        if self.strategy is Strategy.SPREAD:
            # Prefer a constructor this team hasn't picked from yet.
            owned_constructors = {d for ds in already_owned.values() for d in ds}
            fresh = [d for d in available if d.constructor not in owned_constructors]
            return rng.choice(fresh or available)

        if self.strategy is Strategy.BEST_TEAM:
            # Concentrate on the constructor that already scores well for this
            # team — crude, but it produces lopsided rosters like real leagues.
            counts: dict[str, int] = {}
            for drivers in already_owned.values():
                for constructor in drivers:
                    counts[constructor] = counts.get(constructor, 0) + 1
            best = max(counts, key=lambda c: counts.get(c, 0), default=None)
            if best:
                same = [d for d in available if d.constructor == best]
                if same:
                    return rng.choice(same)
            return rng.choice(available)

        # CONSERVATIVE: take from the biggest available constructor pool.
        pools: dict[str, int] = {}
        for driver in available:
            pools[driver.constructor] = pools.get(driver.constructor, 0) + 1
        biggest = max(pools.values())
        return rng.choice([d for d in available if pools[d.constructor] == biggest])


def bot_names(count: int) -> list[str]:
    """Stable bot display names.

    Deterministic and numbered from 1 so re-running ``--attach`` against the
    same league re-uses the same bots instead of creating a second set of
    "Bot 1" rows under different player ids.
    """
    return [f"Bot {i + 1}" for i in range(count)]


def default_roster(
    names: list[str],
    *,
    pace_mix: bool = True,
    seed: int = 7,
) -> list[SimAgent]:
    """A believable league: mixed paces and strategies, deterministic on seed."""
    paces = [Pace.INSTANT, Pace.NORMAL, Pace.NORMAL, Pace.SLOW, Pace.FLAKY]
    strategies = [
        Strategy.RANDOM,
        Strategy.SPREAD,
        Strategy.BEST_TEAM,
        Strategy.CONSERVATIVE,
        Strategy.SPREAD,
    ]

    agents: list[SimAgent] = []
    for index, name in enumerate(names):
        pace = paces[index % len(paces)] if pace_mix else Pace.NORMAL
        strategy = strategies[index % len(strategies)]
        agents.append(SimAgent(name, pace=pace, strategy=strategy, seed=seed))
    return agents
