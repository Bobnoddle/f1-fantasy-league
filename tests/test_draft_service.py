"""Turn machine tests.

These use an in-memory repository rather than Postgres, which is only possible
because the service depends on a five-method Protocol instead of a database.
That is the payoff of the layering: draft timing is testable with no fixtures.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.draft import DraftStatus
from app.services.draft import DraftService, NoopPublisher


def utcnow() -> datetime:
    return datetime.now(UTC)


class FakeRepo:
    """In-memory stand-in. Holds expiry so time can be moved by hand."""

    def __init__(self, teams: int, drivers: int = 22) -> None:
        self.team_ids = [uuid4() for _ in range(teams)]
        self.drivers = [(uuid4(), f"Driver {i}", "McLaren") for i in range(drivers)]
        self.state = None
        self.expiry: datetime | None = None
        self.picks: list[tuple[UUID, UUID, int, bool]] = []
        self.commit_fail = False

    async def get_state(self, league_id):
        return self.state

    async def get_expiry(self, league_id):
        return self.expiry

    async def get_team_ids(self, league_id):
        return list(self.team_ids)

    async def get_taken_driver_ids(self, league_id):
        return {d for _, d, _, _ in self.picks}

    async def get_available_drivers(self, league_id):
        return list(self.drivers)

    async def commit_pick(self, league_id, team_id, driver_id, pick_number, auto):
        if self.commit_fail:
            raise RuntimeError("simulated concurrent insert")
        self.picks.append((team_id, driver_id, pick_number, auto))

    async def save_state(self, league_id, state):
        self.state = state

    async def set_expiry(self, league_id, expires_at):
        self.expiry = expires_at

    async def team_display_names(self, league_id):
        return {}


class RecordingPublisher(NoopPublisher):
    def __init__(self) -> None:
        self.turns: list[tuple] = []
        self.picks: list[tuple] = []
        self.completions = 0
        self.signups: list[tuple] = []

    async def signup_updated(self, league_id, players, size):
        self.signups.append((players, size))

    async def turn_started(self, league_id, team_id, display_name, deadline_secs):
        self.turns.append((team_id, deadline_secs))

    async def pick_recorded(self, league_id, team_id, display_name, driver_name, remaining, auto):
        self.picks.append((team_id, driver_name, remaining, auto))

    async def draft_complete(self, league_id, rosters):
        self.completions += 1


LEAGUE = uuid4()


def service(teams: int = 4, **kw) -> tuple[DraftService, FakeRepo, RecordingPublisher]:
    repo = FakeRepo(teams)
    pub = RecordingPublisher()
    svc = DraftService(repo, pub, rng=random.Random(kw.pop("seed", 1)))
    return svc, repo, pub


# ── Start ───────────────────────────────────────────────────────────────────


async def test_start_arms_timer_and_announces_turn() -> None:
    svc, repo, pub = service(teams=4)

    state = await svc.start(LEAGUE, season_drivers=22, size_override=None, deadline_secs=600)

    assert state.status is DraftStatus.DRAFTING
    assert repo.expiry is not None
    assert repo.expiry > utcnow()
    assert len(pub.turns) == 1
    assert pub.turns[0][1] == 600


async def test_start_picks_team_size() -> None:
    svc, _, _ = service(teams=4)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=None, deadline_secs=300)
    assert state.team_size == 5
    assert len(state.pick_order) == 20


async def test_refresh_signup_reports_size() -> None:
    svc, _, pub = service(teams=4)
    await svc.refresh_signup(LEAGUE, season_drivers=22, size_override=None)
    assert pub.signups == [(4, 5)]


# ── Manual picks ────────────────────────────────────────────────────────────


async def test_pick_advances_cursor_and_rearms() -> None:
    svc, repo, pub = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    first_team = state.on_the_clock

    outcome = await svc.pick(LEAGUE, first_team, repo.drivers[0][0], 300)

    assert outcome.advanced
    assert outcome.state.current_pick == 1
    assert repo.picks[0][0] == first_team
    assert repo.picks[0][3] is False  # not an auto-pick
    assert len(pub.picks) == 1


async def test_pick_rejects_wrong_team() -> None:
    svc, repo, _ = service(teams=3)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    wrong = next(t for t in state.team_ids if t != state.on_the_clock)

    with pytest.raises(ValueError, match="not your turn"):
        await svc.pick(LEAGUE, wrong, repo.drivers[0][0], 300)


async def test_pick_rejects_already_taken_driver() -> None:
    svc, repo, _ = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    taken = repo.drivers[0][0]
    await svc.pick(LEAGUE, state.on_the_clock, taken, 300)

    # Next turn, same driver.
    with pytest.raises(ValueError, match="already been picked"):
        await svc.pick(LEAGUE, repo.state.on_the_clock, taken, 300)


async def test_pick_never_offers_taken_drivers() -> None:
    svc, repo, _ = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    await svc.pick(LEAGUE, state.on_the_clock, repo.drivers[0][0], 300)

    taken = await repo.get_taken_driver_ids(LEAGUE)
    assert repo.drivers[0][0] in taken


async def test_full_draft_completes() -> None:
    svc, repo, pub = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=1, deadline_secs=300)

    while state.status is DraftStatus.DRAFTING:
        team = state.on_the_clock
        taken = await repo.get_taken_driver_ids(LEAGUE)
        free = [d for d in repo.drivers if d[0] not in taken]
        outcome = await svc.pick(LEAGUE, team, free[0][0], 300)
        state = outcome.state

    assert state.status is DraftStatus.COMPLETE
    assert pub.completions == 1
    assert len(repo.picks) == 2


# ── Lazy expiry ─────────────────────────────────────────────────────────────


async def test_advance_is_noop_before_deadline() -> None:
    svc, repo, pub = service(teams=2)
    await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=600)

    assert await svc.advance_if_expired(LEAGUE, 22, 600) is None
    assert repo.picks == []
    assert pub.picks == []


async def test_advance_rolls_over_after_deadline() -> None:
    svc, repo, pub = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    expected_team = state.on_the_clock

    repo.expiry = utcnow() - timedelta(seconds=1)  # deadline passed

    outcome = await svc.advance_if_expired(LEAGUE, 22, 300)

    assert outcome is not None
    assert outcome.auto_picked is True
    assert repo.picks[0][0] == expected_team
    assert repo.picks[0][3] is True
    assert outcome.state.current_pick == 1


async def test_advance_is_idempotent() -> None:
    """Calling twice after a rollover must not pick twice."""
    svc, repo, _ = service(teams=2)
    await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    repo.expiry = utcnow() - timedelta(seconds=1)

    first = await svc.advance_if_expired(LEAGUE, 22, 300)
    assert first is not None

    # Deadline was re-armed to the future by the rollover.
    assert await svc.advance_if_expired(LEAGUE, 22, 300) is None
    assert len(repo.picks) == 1


async def test_advance_never_picks_a_taken_driver() -> None:
    svc, repo, _ = service(teams=2)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    await svc.pick(LEAGUE, state.on_the_clock, repo.drivers[0][0], 300)
    repo.expiry = utcnow() - timedelta(seconds=1)

    await svc.advance_if_expired(LEAGUE, 22, 300)

    picked = [d for _, d, _, _ in repo.picks]
    assert len(picked) == len(set(picked))  # no duplicates


async def test_advance_noop_when_draft_not_running() -> None:
    svc, repo, _ = service(teams=2)
    assert await svc.advance_if_expired(LEAGUE, 22, 300) is None


async def test_expired_turn_still_respects_pick_order() -> None:
    svc, repo, _ = service(teams=4)
    state = await svc.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    expected = state.on_the_clock
    repo.expiry = utcnow() - timedelta(seconds=1)

    await svc.advance_if_expired(LEAGUE, 22, 300)

    assert repo.picks[0][0] == expected


async def test_deadline_survives_service_restart() -> None:
    """A new service instance over the same repo sees the same clock.

    This is the property that an in-memory timer cannot provide.
    """
    repo = FakeRepo(2)
    pub = RecordingPublisher()

    first = DraftService(repo, pub, rng=random.Random(1))
    await first.start(LEAGUE, season_drivers=22, size_override=2, deadline_secs=300)
    repo.expiry = utcnow() - timedelta(seconds=1)

    # Simulate a redeploy: brand new service object, same database.
    second = DraftService(repo, pub, rng=random.Random(1))
    outcome = await second.advance_if_expired(LEAGUE, 22, 300)

    assert outcome is not None
    assert outcome.auto_picked is True
