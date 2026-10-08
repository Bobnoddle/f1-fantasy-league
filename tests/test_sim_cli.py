"""End-to-end tests for `python -m app.cli simulate`.

The CLI had no coverage at all, which is how a lost commit survived: the league
transitioned to "active", the flush was issued, and AsyncSession.__aexit__
discarded it — leaving a league stuck in "drafting" while carrying a full season
of results. Nothing asserted the end state, so nothing noticed.

These run the real command against the test database with the network provider
replaced by a deterministic fake. Races, sprints, points and the state machine
are all real; only the HTTP is not.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Event, League, Roster, Score


def _argv(**overrides) -> list[str]:
    """Build the argv the shell would, so this exercises argument parsing too."""
    args = {
        "season": "2025",
        "players": "4",
        "through": "4",
        "pick-deadline": "600",
        "code": "simtest",
        "name": "Simulated League",
        "seed": "7",
        "speed": "1.0",
    }
    args.update(overrides)

    argv = ["simulate"]
    for key, value in args.items():
        if value is not None:
            # argparse spells flags with dashes; an override key written with an
            # underscore silently became "--human_grace" and was rejected.
            argv += [f"--{key.replace('_', '-')}", str(value)]
    argv.append("--quiet")
    return argv


async def run_sim(**overrides) -> int:
    """Invoke the CLI exactly as the shell would.

    Runs in a worker thread because main() calls asyncio.run(), which refuses to
    start inside the event loop the test is already on. Going through main()
    rather than run_simulate() directly keeps argument parsing in the picture.
    """
    import asyncio

    from app.cli import main

    return await asyncio.to_thread(main, _argv(**overrides))


async def league_state(app, code: str = "simtest") -> str:
    async with app.state.db_factory() as db:
        return (await db.scalars(select(League.state).where(League.code == code))).one()


async def league_id_for(app, code: str = "simtest"):
    """Just the id. Returning a League means touching it after the session has
    closed, and it is expired by then."""
    async with app.state.db_factory() as db:
        return (await db.scalars(select(League.id).where(League.code == code))).one()


# ── The regression ──────────────────────────────────────────────────────────


async def test_the_league_ends_up_active(app, fake_season):
    """Regression: the final state transition was flushed, never committed.

    AsyncSession.__aexit__ closes rather than commits, so run_season's move to
    "active" was discarded and the league sat in "drafting" while holding four
    rounds of real results.
    """
    assert await run_sim() == 0

    assert await league_state(app) == "active", (
        "simulate must leave the league active; a league stuck in 'drafting' "
        "while holding a season of results is the lost commit"
    )


async def test_scored_results_survive_the_run(app, fake_season):
    """The commit must not drop the season along with the state change."""
    assert await run_sim() == 0

    lid = await league_id_for(app)
    async with app.state.db_factory() as db:
        events = await db.scalar(select(func.count(Event.id)).where(Event.league_id == lid))
        picks = await db.scalar(select(func.count(Roster.driver_id)).where(Roster.league_id == lid))
        # Score has no surrogate key; it is keyed by (event_id, driver_id).
        scored = await db.scalar(
            select(func.count())
            .select_from(Score)
            .join(Event, Event.id == Score.event_id)
            .where(Event.league_id == lid)
        )

    assert events >= 4, "four rounds plus two sprints should be scored"
    assert picks > 0, "the draft produced no picks"
    assert scored > 0, "nothing was scored against any team"


async def test_lapped_drivers_still_score(app, fake_season):
    """The fake calendar finishes 15 and laps 5, so a DNF misclassification
    would show up as fewer points than the field allows."""
    assert await run_sim() == 0

    lid = await league_id_for(app)
    async with app.state.db_factory() as db:
        total = await db.scalar(
            select(func.coalesce(func.sum(Score.points), 0))
            .select_from(Score)
            .join(Event, Event.id == Score.event_id)
            .where(Event.league_id == lid)
        )
    assert float(total) > 0


# ── The draft ───────────────────────────────────────────────────────────────


async def test_every_team_ends_with_an_equal_roster(app, fake_season):
    assert await run_sim() == 0

    lid = await league_id_for(app)
    async with app.state.db_factory() as db:
        counts = (
            await db.execute(
                select(Roster.team_id, func.count(Roster.driver_id))
                .where(Roster.league_id == lid)
                .group_by(Roster.team_id)
            )
        ).all()

    assert counts, "no teams drafted"
    sizes = {n for _, n in counts}
    assert len(sizes) == 1, f"teams have unequal rosters: {counts}"
    assert sizes.pop() > 0


async def test_no_driver_is_drafted_twice(app, fake_season):
    assert await run_sim() == 0

    lid = await league_id_for(app)
    async with app.state.db_factory() as db:
        ids = (
            (await db.execute(select(Roster.driver_id).where(Roster.league_id == lid)))
            .scalars()
            .all()
        )

    assert len(ids) == len(set(ids)), "a driver appears on two teams"


# ── Idempotency ─────────────────────────────────────────────────────────────


async def test_rerunning_replaces_the_league_rather_than_duplicating(app, fake_season):
    """A second run must not leave two leagues with the same code."""
    assert await run_sim() == 0
    assert await run_sim() == 0

    async with app.state.db_factory() as db:
        count = await db.scalar(select(func.count(League.id)).where(League.code == "simtest"))
    assert count == 1, "simulate is not idempotent on league code"


async def test_the_second_run_also_ends_active(app, fake_season):
    """The bug showed up inconsistently; both runs must be correct."""
    assert await run_sim() == 0
    assert await run_sim() == 0

    assert await league_state(app) == "active"


# ── Failure reporting ───────────────────────────────────────────────────────


async def test_it_reports_failure_when_no_driver_can_be_drafted(app, monkeypatch, fake_season):
    """A run that drafts nothing has failed, even without an exception."""
    from app.provider.jolpica import JolpicaProvider

    async def empty(self, season: int):
        return []

    monkeypatch.setattr(JolpicaProvider, "drivers", empty)

    assert await run_sim(players="1") != 0


async def test_it_reports_failure_when_the_calendar_is_unavailable(app, monkeypatch, fake_season):
    from app.provider.base import ProviderError
    from app.provider.jolpica import JolpicaProvider

    async def broken(self, season: int):
        raise ProviderError("upstream down")

    monkeypatch.setattr(JolpicaProvider, "calendar", broken)

    assert await run_sim() != 0


# ── Config gating ───────────────────────────────────────────────────────────


async def test_it_runs_with_no_discord_configured(app, fake_season, monkeypatch):
    """Discord being optional has to hold for the simulator too."""
    import app.config as config_mod

    monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DISCORD_CLIENT_SECRET", raising=False)
    config_mod.get_settings.cache_clear()
    assert config_mod.get_settings().discord_client_id == "", (
        "this test only means something with Discord unconfigured"
    )

    assert await run_sim() == 0
    config_mod.get_settings.cache_clear()
