"""Draft flow tests.

The draft is the only part of the app with a hard latency budget, so it is also
the part where the lazy-expiry design either works or quietly does not. Every
test here goes through the real routes and a real database.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import add_players, close_all, make_league, post, sign_in


async def start_draft(admin, code: str, names: list[str]):
    """Open signup, add players, close, start. Returns a live client per player."""
    await post(admin, f"/l/{code}/admin/action", {"action": "open-signup"})
    players = await add_players(admin, names, code)
    await post(admin, f"/l/{code}/admin/action", {"action": "close-signup"})
    resp = await post(admin, f"/l/{code}/admin/action", {"action": "start-draft"})
    assert resp.status_code == 303, resp.text
    return {"Admin": admin, **players}


async def on_the_clock(players: dict, code: str):
    """Return (name, client) for whoever the picker opens for."""
    for name, c in players.items():
        if (await c.get(f"/l/{code}/draft/pick")).status_code == 200:
            return name, c
    return None, None


# ── Lifecycle buttons ───────────────────────────────────────────────────────


async def test_turn_is_checked_before_the_driver(client):
    """A player who is not on the clock must be told that, even if the driver
    id they sent is also nonsense. The turn is the more useful message."""
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    for _name, c in players.items():
        resp = await post(c, f"/l/{code}/draft/pick", {"driver_id": str(uuid.uuid4())})
        if "not-your-turn" in resp.headers.get("location", ""):
            break
    else:
        pytest.fail("no player was told it was not their turn")

    await close_all(players)


async def test_a_draft_cannot_start_until_two_players_join(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    resp = await post(client, f"/l/{code}/admin/action", {"action": "start-draft"})
    assert "need-2-players" in resp.headers["location"]

    # The panel explains it when you follow the redirect.
    resp = await client.get(resp.headers["location"])
    assert "At least 2 players" in resp.text


async def test_starting_a_draft_persists_the_seed_order(client):
    """draft_state holds the order, but the hub and panel read team.draft_order.

    Found by screenshot: every player showed "—" all draft long.
    """
    from sqlalchemy import select

    from app.models import League, Team

    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    app = client._transport.app
    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        teams = (await db.scalars(select(Team).where(Team.league_id == league.id))).all()

    orders = [t.draft_order for t in teams]
    assert len(orders) == 3  # the creator is a team too
    assert all(o is not None for o in orders), "draft_order was never persisted"
    assert sorted(orders) == [1, 2, 3]

    await close_all(players)


async def test_the_hub_shows_the_persisted_order(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    resp = await client.get(f"/l/{code}/draft")
    assert resp.status_code == 200
    assert "picking" in resp.text.lower()

    await close_all(players)


# ── Turn gating ─────────────────────────────────────────────────────────────


async def test_only_the_player_on_the_clock_sees_the_picker(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya", "Marco"])

    statuses = {
        name: (await c.get(f"/l/{code}/draft/pick")).status_code for name, c in players.items()
    }
    assert sorted(statuses.values()) == [200, 303, 303, 303], statuses

    await close_all(players)


async def test_being_off_the_clock_redirects_with_a_reason(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    for _name, c in players.items():
        resp = await c.get(f"/l/{code}/draft/pick")
        if resp.status_code == 303 and "not-your-turn" in resp.headers["location"]:
            break
    else:
        pytest.fail("nobody was told it was not their turn")

    await close_all(players)


async def test_a_player_outside_the_league_cannot_pick(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam"])

    outsider = client._transport.app  # noqa: F841 - readability only
    from tests.helpers import sibling

    other = sibling(client)
    await post(other, "/login", {"display_name": "Outsider", "next": "/me"})

    resp = await post(other, f"/l/{code}/draft/pick", {"driver_id": str(uuid.uuid4())})
    assert resp.status_code == 303
    assert "/draft" in resp.headers["location"]

    await other.aclose()
    await close_all(players)


# ── Picking ─────────────────────────────────────────────────────────────────


async def test_the_picker_lists_every_unpicked_driver(client):
    """No 25-option cap, unlike a Discord select menu."""
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam"])

    _, on_clock = await on_the_clock(players, code)
    assert on_clock is not None

    page = await on_clock.get(f"/l/{code}/draft/pick")
    assert page.status_code == 200
    assert page.text.count('name="driver_id"') == 20
    assert "you can close this tab" in page.text.lower()

    await close_all(players)


async def test_picking_advances_the_cursor(client):
    from sqlalchemy import select

    from app.models import Draft, League

    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam"])

    name, on_clock = await on_the_clock(players, code)
    page = await on_clock.get(f"/l/{code}/draft/pick")
    driver_id = page.text.split('name="driver_id" value="')[1].split('"')[0]

    resp = await post(on_clock, f"/l/{code}/draft/pick", {"driver_id": driver_id})
    assert resp.status_code == 303
    assert "picked" in resp.headers["location"]

    app = client._transport.app
    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        draft = await db.get(Draft, league.id)
        assert draft.current_pick == 1

    await close_all(players)


async def test_picking_out_of_turn_is_refused(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    refused = 0
    for _name, c in players.items():
        resp = await post(c, f"/l/{code}/draft/pick", {"driver_id": str(uuid.uuid4())})
        if resp.status_code == 303 and "not-your-turn" in resp.headers["location"]:
            refused += 1

    assert refused == len(players) - 1, "exactly one player is on the clock"

    await close_all(players)


async def test_a_picked_driver_disappears_from_the_picker(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    _, first = await on_the_clock(players, code)
    page = await first.get(f"/l/{code}/draft/pick")
    driver_id = page.text.split('name="driver_id" value="')[1].split('"')[0]
    await post(first, f"/l/{code}/draft/pick", {"driver_id": driver_id})

    _, second = await on_the_clock(players, code)
    nxt = await second.get(f"/l/{code}/draft/pick")
    assert driver_id not in nxt.text
    assert nxt.text.count('name="driver_id"') == 19

    await close_all(players)


# ── Lazy expiry through the web layer ───────────────────────────────────────


async def _expire_now(client, code: str) -> None:
    """Push the deadline into the past, the way a real lapse would."""
    from sqlalchemy import text

    app = client._transport.app
    async with app.state.db_factory() as db:
        await db.execute(
            text(
                """
                UPDATE draft SET pick_expires_at = now() - interval '1 minute'
                WHERE league_id = (SELECT id FROM league WHERE code = :c)
                """
            ),
            {"c": code},
        )
        await db.commit()


async def test_an_expired_pick_auto_picks_when_anyone_loads_the_draft(client):
    """The core of the timeout design, exercised through a real request.

    Nothing schedules this. The deadline lives in the database and the next
    page load notices it has passed.
    """
    from sqlalchemy import select

    from app.models import League, Roster

    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    _, on_clock = await on_the_clock(players, code)
    picks_before = await _count_roster(client, code)
    assert picks_before == 0

    await _expire_now(client, code)

    # Any player's page load settles the lapsed pick.
    resp = await client.get(f"/l/{code}/draft")
    assert resp.status_code == 200

    picks_after = await _count_roster(client, code)
    assert picks_after == 1, "the lapsed pick should have auto-resolved"

    app = client._transport.app
    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        row = (await db.scalars(select(Roster).where(Roster.league_id == league.id))).one()
        assert row.auto_picked is True

    await close_all(players)


async def test_expiry_is_idempotent_across_repeated_loads(client):
    """Two players loading at once must not produce two auto-picks."""
    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    await _expire_now(client, code)

    for c in players.values():
        await c.get(f"/l/{code}/draft")

    assert await _count_roster(client, code) == 1

    await close_all(players)


async def _count_roster(client, code: str) -> int:
    from sqlalchemy import func, select

    from app.models import League, Roster

    app = client._transport.app
    async with app.state.db_factory() as db:
        league = (await db.scalars(select(League).where(League.code == code))).one()
        return await db.scalar(
            select(func.count(Roster.driver_id)).where(Roster.league_id == league.id)
        )
