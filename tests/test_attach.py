"""Playing a league locally: you plus bots.

``simulate`` always deleted and rebuilt its league, so there was no way to play
one yourself — the simulator's players were the whole field. ``--attach`` adds
bots to a league you created and joined, and leaves the clock to you.

These run the real CLI against the test database with a fixed season.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models import Driver, League, Roster, Team
from tests.helpers import make_league, post, sign_in
from tests.test_sim_cli import run_sim


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _human_league(client, name: str = "My League", season: int = 2025) -> str:
    """A league the signed-in player created and joined, exactly as the browser does."""
    await sign_in(client, "Dave")
    return await make_league(client, name=name, season=season)


# ── Attaching ───────────────────────────────────────────────────────────────


async def test_attach_reuses_the_league_and_adds_bots(app, client, fake_season):
    code = await _human_league(client)

    assert await run_sim(attach=code, players="6", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        teams = (await db.scalars(select(Team).where(Team.league_id == league.id))).all()

    names = {t.display_name for t in teams}
    assert "Dave" in names, "attaching removed the human from their own league"
    assert sum(1 for n in names if n.startswith("Bot ")) == 6


async def test_attach_never_deletes_the_league(app, client, fake_season):
    """The pre-attach behaviour was to delete and recreate, which would have
    thrown away the human's team and admin rights."""
    code = await _human_league(client)

    async with app.state.db_factory() as db:
        before = await db.get(
            League, (await db.scalars(select(League).where(League.code == code))).one().id
        )

    assert await run_sim(attach=code, players="4", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        still = await db.get(League, before.id)

    assert still is not None, "the league row was destroyed"
    assert still.admin_player_id == before.admin_player_id


async def test_attach_reuses_the_same_bots_on_a_second_run(app, client, fake_season):
    """Deterministic names, so re-running does not create a second Bot 1.

    The second run also has nothing to draft: the roster is full. It
    used to restart the draft anyway — resetting the cursor on a full
    roster — which left the draft "drafting" with no drivers left to
    pick, and could spin the turn budget trying.
    """
    from app.models import Draft

    code = await _human_league(client)
    await run_sim(attach=code, players="3", through="1", human_grace="1")

    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League.id).where(League.code == code))).one()
        finished = await db.get(Draft, league_id)
        assert finished.status == "complete"
        cursor_before = finished.current_pick
        order_before = list(finished.pick_order)

    assert await run_sim(attach=code, players="3", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        count = await db.scalar(
            select(func.count(Team.id))
            .join(League, League.id == Team.league_id)
            .where(League.code == code)
        )
        draft = await db.get(Draft, league_id)

    assert count == 4, f"expected Dave plus 3 bots, found {count} teams"
    # The finished draft was left exactly as it was: not restarted,
    # not rewound, not stranded in "drafting" on a full roster.
    assert draft.status == "complete", (
        f"a second run restarted a finished draft (status={draft.status})"
    )
    assert draft.current_pick == cursor_before
    assert list(draft.pick_order) == order_before


async def test_attach_to_a_missing_league_fails_clearly(app, client, fake_season):
    assert await run_sim(attach="no-such-league", players="2", through="1") != 0


async def test_signup_only_fills_the_roster_without_starting_the_draft(app, client, fake_season):
    """`--attach` alone opens, closes and plays the draft — consuming signup,
    which is the admin's to run from the panel. `--signup-only` stops after
    joining the bots."""
    from app.models import Draft, League, Team

    code = await _human_league(client)

    assert await run_sim(attach=code, players="6", signup_only=True) == 0

    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        teams = (await db.scalars(select(Team).where(Team.league_id == league.id))).all()
        draft = await db.get(Draft, league.id)

    names = {t.display_name for t in teams}
    assert "Dave" in names
    assert sum(1 for n in names if n.startswith("Bot ")) == 6

    # The draft was not started, and the season was not scored.
    assert league.state in ("created", "signup_open", "draft_ready"), league.state
    assert draft is None or draft.status in ("pending", "draft_ready"), (
        "signup-only started the draft"
    )


async def test_signup_only_is_idempotent(app, client, fake_season):
    """Running it twice must not double the bots."""
    from app.models import Team

    code = await _human_league(client)
    await run_sim(attach=code, players="6", signup_only=True)
    await run_sim(attach=code, players="6", signup_only=True)

    async with app.state.db_factory() as db:
        count = await db.scalar(
            select(func.count(Team.id))
            .join(League, League.id == Team.league_id)
            .where(League.code == code)
        )
    assert count == 7, f"expected Dave plus 6 bots, found {count} teams"


async def test_attach_refuses_a_season_mismatch(app, client, fake_season):
    """Silently seeding a different season would leave an undraftable league."""
    code = await _human_league(client)

    assert await run_sim(attach=code, season="2024", players="2", through="1") != 0


async def test_attach_defaults_to_the_leagues_season(app, client, fake_season):
    """The CLI's ``--season`` default used to be 2025, so the documented
    attach command against a 2026 league died on a mismatch before a
    single bot picked — no pick, no error the user could act on, just
    an exit. Omitting ``--season`` must mean "the league's own year"."""
    code = await _human_league(client, season=2026)

    # season=None omits the flag, so the CLI must resolve the
    # league's own year rather than its 2025 default.
    assert await run_sim(attach=code, season=None, players="2", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        drivers = await db.scalar(
            select(func.count()).select_from(Driver).where(Driver.season_year == league.season_year)
        )
        picks = await db.scalar(
            select(func.count(Roster.driver_id)).where(Roster.league_id == league.id)
        )

    assert league.season_year == 2026
    assert drivers == 20, "the league's own season was not seeded"
    assert picks > 0, "no bot picked — the draft never ran"


async def test_attaching_to_a_running_draft_resumes_it(app, client, fake_season):
    """The admin panel starts the draft; the simulator attaches afterwards.

    ``run_draft`` used to call ``DraftService.start`` unconditionally,
    which rebuilt the pick order from scratch and reset the cursor —
    reshuffling the order the panel had announced and orphaning every
    pick already made.
    """
    from app.models import Draft
    from app.repo.postgres import LeagueRepo, PostgresDraftRepo
    from app.services.draft import DraftService

    code = await _human_league(client)

    async with app.state.db_factory() as db:
        repo = LeagueRepo(db)
        league = await repo.by_code(code)
        for name in ("Bot 1", "Bot 2"):
            player = await repo.upsert_player(provider="guest", external_id=None, display_name=name)
            await repo.join(league.id, player)

        drepo = PostgresDraftRepo(db)
        service = DraftService(drepo)
        started = await service.start(league.id, 20, None, 600)
        await repo.set_state(league.id, "drafting")

        # One pick, by whoever the shuffle put on the clock.
        pool = [d for d, _, _ in await drepo.get_available_drivers(league.id)]
        first_team = started.on_the_clock
        await service.pick(league.id, first_team, pool[0], 600)
        await db.commit()

        state = await drepo.get_state(league.id)
        order_before = list(state.pick_order)
        cursor_before = state.current_pick

    assert cursor_before == 1, "the opening pick was not recorded"

    assert await run_sim(attach=code, players="2", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League.id).where(League.code == code))).one()
        draft = await db.get(Draft, league_id)
        roster = (
            await db.execute(
                select(Roster.team_id, Roster.driver_id)
                .where(Roster.league_id == league_id)
                .order_by(Roster.pick_number)
            )
        ).all()
        counts = (
            await db.execute(
                select(Roster.team_id, func.count(Roster.driver_id))
                .where(Roster.league_id == league_id)
                .group_by(Roster.team_id)
            )
        ).all()

    # The order the panel announced is the order that was played.
    assert [str(t) for t in draft.pick_order] == [str(t) for t in order_before], (
        "attaching reshuffled the pick order"
    )
    # The opening pick survived, in place, on the same team.
    assert (first_team, pool[0]) in roster
    assert roster[0] == (first_team, pool[0]), "the first pick was replayed or moved"
    # Equal rosters: no team picked twice or was skipped.
    assert {n for _, n in counts} == {6}, counts


async def test_attach_does_not_rewind_a_drafting_league(app, client, fake_season):
    """attach() used to set the league back to draft_ready, which re-opens
    signup on a league whose draft is already running."""
    from app.models import Draft
    from app.repo.postgres import LeagueRepo, PostgresDraftRepo
    from app.services.draft import DraftService

    code = await _human_league(client)

    async with app.state.db_factory() as db:
        repo = LeagueRepo(db)
        league = await repo.by_code(code)
        # A draft needs a field: give the league two bots before
        # starting it, exactly as signup would have.
        for name in ("Bot 1", "Bot 2"):
            player = await repo.upsert_player(provider="guest", external_id=None, display_name=name)
            await repo.join(league.id, player)
        drepo = PostgresDraftRepo(db)
        service = DraftService(drepo)
        await service.start(league.id, 20, None, 600)
        await repo.set_state(league.id, "drafting")
        await db.commit()

    assert await run_sim(attach=code, players="2", signup_only=True) == 0

    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League.id).where(League.code == code))).one()
        state = (await db.scalars(select(League.state).where(League.id == league_id))).one()
        draft = await db.get(Draft, league_id)

    assert state == "drafting", f"attach rewound the league to {state!r}"
    assert draft.status == "drafting"


# ── The human gets a real turn ──────────────────────────────────────────────


async def test_the_picker_opens_for_the_human_on_the_clock(app, client):
    """The claim that matters: your page can actually pick.

    Two bugs had to be fixed for this to hold. The simulator only committed at
    the very end, so the web app — on its own connection — still saw a pending
    draft and refused to open the picker. And the human's turns were auto-picked
    instantly because the simulated clock had already run past the deadline.

    Deliberately deterministic rather than racing a background CLI: the draft is
    advanced until the human is on the clock, then the picker is requested. No
    polling, no sleeps, so this cannot flake.
    """
    from app.repo.postgres import LeagueRepo, PostgresDraftRepo
    from app.services.draft import DraftService
    from app.services.scoring import seed_season

    code = await _human_league(client)

    async with app.state.db_factory() as db:
        from app.provider.jolpica import JolpicaProvider

        repo = LeagueRepo(db)
        league = await repo.by_code(code)
        drivers = await seed_season(db, JolpicaProvider(), 2025)

        # Give the bots a team each, exactly as attach does.
        for name in ("Bot 1", "Bot 2", "Bot 3"):
            player = await repo.upsert_player(provider="guest", external_id=None, display_name=name)
            await repo.join(league.id, player)

        human_team = (
            await db.scalars(
                select(Team).where(Team.league_id == league.id, Team.display_name == "Dave")
            )
        ).one()

        drepo = PostgresDraftRepo(db)
        service = DraftService(drepo)
        state = await service.start(league.id, drivers, None, 600)
        await db.commit()

        # Put the human on the clock: advance until it is their team's turn.
        names = await drepo.team_display_names(league.id)
        guard = 0
        while state.on_the_clock != human_team.id and guard < 40:
            guard += 1

            pool = [
                d
                for d, _, _ in await drepo.get_available_drivers(league.id)
                if d not in (await drepo.get_taken_driver_ids(league.id))
            ]
            assert pool, "ran out of drivers before reaching the human"
            await service.pick(league.id, state.on_the_clock, pool[0], 600)
            state = await drepo.get_state(league.id)
        await db.commit()

        assert state.on_the_clock == human_team.id, "the human never reached the front"
        assert names[state.on_the_clock] == "Dave"

    # A separate connection — the web app — must see it, then serve the picker.
    resp = await client.get(f"/l/{code}/draft/pick")
    assert resp.status_code == 200, "the picker did not open for the human on the clock"
    assert 'name="driver_id"' in resp.text

    # And the pick goes through.
    driver_id = resp.text.split('name="driver_id" value="')[1].split('"')[0]
    picked = await post(client, f"/l/{code}/draft/pick", {"driver_id": driver_id})
    assert picked.status_code == 303
    assert "picked" in picked.headers["location"]

    async with app.state.db_factory() as db:
        owner = await db.scalar(select(Roster.team_id).where(Roster.driver_id == UUID(driver_id)))
    assert owner == human_team.id, "the pick was not credited to the human"


async def test_the_humans_turn_is_left_to_them(app, client, monkeypatch):
    """The simulator must not pick for a team nobody is simulating."""
    code = await _human_league(client)

    waited_for: list[str] = []

    from app.sim.runner import Simulator

    original = Simulator._await_human

    async def spy(self, league, team_id, names):
        waited_for.append(names.get(team_id, "?"))
        return await original(self, league, team_id, names)

    monkeypatch.setattr(Simulator, "_await_human", spy)

    assert await run_sim(attach=code, players="4", through="1", human_grace="1") == 0

    assert waited_for, "the draft never handed the clock to anyone"
    assert set(waited_for) == {"Dave"}, f"picked for the wrong teams: {waited_for}"


async def test_the_draft_finishes_even_if_the_human_walks_away(app, client, fake_season):
    """A short grace window settles the pick, so the roster still completes."""
    code = await _human_league(client)

    assert await run_sim(attach=code, players="4", through="1", human_grace="1") == 0

    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League).where(League.code == code))).one().id

    # Every team has the same number of drivers, human included.
    async with app.state.db_factory() as db:
        counts = (
            await db.execute(
                select(Roster.team_id, func.count(Roster.driver_id))
                .where(Roster.league_id == league_id)
                .group_by(Roster.team_id)
            )
        ).all()

    sizes = {n for _, n in counts}
    assert len(sizes) == 1, f"rosters are unequal, so a pick went missing: {counts}"


async def test_the_human_scores_points_too(app, client, fake_season):
    """They are in the league properly, not on the sidelines."""
    code = await _human_league(client)

    assert await run_sim(attach=code, players="4", through="2", human_grace="1") == 0

    from app.services.scoring import season_standings

    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League).where(League.code == code))).one().id
        standings = await season_standings(db, league_id)

    me = [s for s in standings if s.display_name == "Dave"]
    assert me, "the human is missing from the standings"
    assert me[0].points > 0, "the human scored nothing across two rounds"


# ── Bot naming ──────────────────────────────────────────────────────────────


def test_bot_names_are_deterministic():
    from app.sim.agents import bot_names

    assert bot_names(3) == ["Bot 1", "Bot 2", "Bot 3"]
    assert bot_names(3) == bot_names(3)


async def _roster_count(app, code: str) -> int:
    """Count picks on a separate connection, the way the web app sees them.

    The whole point of committing each turn: an uncommitted draft is invisible
    here, which is exactly the bug that stopped the picker opening.
    """
    async with app.state.db_factory() as db:
        league_id = (await db.scalars(select(League).where(League.code == code))).one().id
        return await db.scalar(
            select(func.count(Roster.driver_id)).where(Roster.league_id == league_id)
        )
