"""The fixed season the simulator tests play against.

Shared rather than duplicated: two test modules need it, and pytest fixtures
defined in a test module are not visible to the next one — which is a
confusing way to lose a fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

CONSTRUCTORS = ["Alpha Racing", "Beta Racing", "Gamma Racing"]

#: Rounds that have a sprint weekend, so the sprint scoring path is exercised.
SPRINT_ROUNDS = (3, 6)

SCHEDULE = [
    ("Australian Grand Prix", 1, 0),
    ("Chinese Grand Prix", 2, 14),
    ("Japanese Grand Prix", 3, 28),
    ("Bahrain Grand Prix", 4, 42),
]

#: Round 1 is a full 20-car grid with five drivers lapped, which is what makes
#: the "lapped is still classified" scoring rule reachable from a test.
GRID_SIZE = 20


def calendar():
    from app.provider.base import Race

    return [
        Race(
            round=round_number,
            name=name,
            date=datetime(2025, 3, 1, tzinfo=UTC) + timedelta(days=days),
            sprint_date=(
                datetime(2025, 3, 1, tzinfo=UTC) + timedelta(days=days - 1)
                if round_number in SPRINT_ROUNDS
                else None
            ),
        )
        for name, round_number, days in SCHEDULE
    ]


def drivers(season: int):
    from app.provider.base import Driver

    return [
        Driver(
            code=f"D{i:02d}",
            name=f"Driver {i:02d}",
            constructor=CONSTRUCTORS[i % len(CONSTRUCTORS)],
        )
        for i in range(GRID_SIZE)
    ]


def event_result(round_number: int, kind):
    from app.provider.base import DriverResult, EventResult, Status

    name = next(n for n, r, _ in SCHEDULE if r == round_number)

    return EventResult(
        season=2025,
        round=round_number,
        kind=kind,
        name=f"{name} (sprint)" if kind == "sprint" else name,
        results=[
            DriverResult(
                code=f"D{i:02d}",
                # Lapped drivers are still classified. Scoring them as a DNF was
                # the highest-impact bug in this project, so the fixture
                # deliberately contains them.
                status=Status.FINISHED if i < 15 else Status.LAPPED,
                position=i + 1,
                grid=i + 1,
                fastest_lap=(i == 3),
                quali=i // 2 + 1,
            )
            for i in range(GRID_SIZE)
        ],
    )
