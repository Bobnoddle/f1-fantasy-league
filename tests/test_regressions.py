"""Regression tests for defects found by auditing the web layer.

Every test here corresponds to something that was actually wrong, verified
against a running app rather than inferred. The comments say what broke, because
a test named ``test_csrf_is_enforced`` tells you nothing about why it exists.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from tests.helpers import (
    add_players,
    close_all,
    csrf_of,
    make_league,
    post,
    sibling,
    sign_in,
)

# ── Account takeover via /signup ────────────────────────────────────────────
# CRITICAL. create_league accepted a `discord_id` form field and handed it to
# upsert_player, which returns the *existing* player row for a matching
# (provider, external_id). Posting a victim's snowflake therefore signed the
# attacker in as the victim — inheriting their leagues and every league they
# administer. Verified by decoding the attacker's cookie and finding the
# victim's player_id in it.


async def test_signup_cannot_claim_a_discord_identity(client):
    """A typed Discord id is not a verified one.

    Reproduces the original exploit exactly: an *anonymous* POST to /signup
    carrying the victim's snowflake. Anonymous matters — create_league only
    reaches upsert_player when optional_player() is None, so signing in first
    skips the vulnerable branch and the test passes against vulnerable code.
    """
    app = client._transport.app
    from app.models import Player

    # A victim who genuinely signed in through Discord OAuth.
    async with app.state.db_factory() as db:
        victim = Player(provider="discord", external_id="123456789012345678", display_name="Victim")
        db.add(victim)
        await db.commit()
        victim_id = victim.id

    # No sign-in. Fresh jar, no session.
    attacker = sibling(client)

    await post(
        attacker,
        "/signup",
        {
            "name": "Attacker League",
            "season": "2025",
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Attacker",
            # The field the route used to accept.
            "discord_id": "123456789012345678",
        },
        csrf_from="/signup",
    )

    async with app.state.db_factory() as db:
        victim_row = await db.get(Player, victim_id)
        assert victim_row.display_name == "Victim", (
            "signup overwrote a Discord player's display name via upsert_player"
        )

    # And the attacker must not be holding the victim's identity.
    # Decoded with the app's own codec: the secret comes from the environment,
    # so a hardcoded one silently decodes to nothing under CI.
    from app.web.session import COOKIE

    jar = attacker.cookies.get(COOKIE)
    assert jar, "the attacker was not signed in at all"
    assert app.state.codec.decode(jar).player_id != victim_id, (
        "the attacker's session carries the victim's player id"
    )

    await attacker.aclose()


async def test_a_typed_discord_id_creates_a_guest_not_a_discord_player(client):
    from app.models import Player

    # Anonymous, for the same reason as the test above.
    await post(
        client,
        "/signup",
        {
            "name": "Typing League",
            "season": "2025",
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Typist",
            "discord_id": "999888777666555444",
        },
        csrf_from="/signup",
    )

    app = client._transport.app
    async with app.state.db_factory() as db:
        players = (await db.scalars(select(Player))).all()

    # Either the field was ignored entirely, or it was ignored in favour of a
    # guest row. What must not happen is a discord row keyed on a typed id.
    assert not [
        p for p in players if p.provider == "discord" and p.external_id == "999888777666555444"
    ], "a typed discord_id produced a verified-looking Discord player"


# ── CSRF ─────────────────────────────────────────────────────────────────────
# verify_csrf existed and was never called from anywhere in app/. All nine POSTs
# were reachable cross-site. SameSite=Lax mitigates the classic form attack but
# not same-site/subdomain attackers or non-browser clients.


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("post", "/login", {"display_name": "Mallory", "next": "/me"}),
        ("post", "/logout", {}),
        ("post", "/signup", {"name": "X", "season": "2025", "display_name": "Mallory"}),
    ],
)
async def test_unsafe_requests_without_a_token_are_refused(client, method, path, data):
    await sign_in(client, "Admin")

    resp = await getattr(client, method)(path, data=data)
    assert resp.status_code == 403, f"{path} accepted a request with no CSRF token"


async def test_a_forged_csrf_token_is_refused(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    resp = await client.post(
        f"/l/{code}/admin/action",
        data={"action": "reset", "csrf_token": "totally-made-up"},
    )
    assert resp.status_code == 403


async def test_another_players_csrf_token_is_refused(client):
    """The token is per session, so one player's must not work for another's."""
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = sibling(client)
    await post(other, "/login", {"display_name": "Sam", "next": "/me"})
    their_token = csrf_of((await other.get("/me")).text)

    resp = await client.post(
        f"/l/{code}/admin/action",
        data={"action": "reset", "csrf_token": their_token},
    )
    assert resp.status_code == 403

    await other.aclose()


async def test_get_requests_do_not_need_a_token(client):
    """Reads stay reachable — the guard must not break the public pages."""
    for path in ("/", "/leagues", "/rules", "/pricing", "/login", "/signup", "/health"):
        assert (await client.get(path)).status_code == 200, path


async def test_the_rendered_token_is_a_real_value(client):
    """Forms used to render value="None".

    league_context returned csrf_token: None and every router spread it *after*
    the real value, so the merge overwrote a good token with None. Enforcing CSRF
    made that visible immediately.
    """
    await sign_in(client, "Admin")
    code = await make_league(client)

    for path in (f"/l/{code}", f"/l/{code}/admin", f"/l/{code}/join", "/me"):
        import re

        body = (await client.get(path)).text
        for value in re.findall(r'name="csrf_token"\s+value="([^"]*)"', body):
            assert value, f"{path} rendered an empty CSRF token"
            assert value != "None", f"{path} rendered a null CSRF token"
            assert len(value) > 20, f"{path} rendered a suspiciously short token"


async def test_anonymous_visitors_get_a_stable_token(client):
    """Anonymous forms are guarded too, so the token has to survive a request.

    Without a cookie, the token was regenerated per request and every anonymous
    POST would have failed.
    """
    first = csrf_of((await client.get("/login")).text)
    second = csrf_of((await client.get("/login")).text)
    assert first == second, "the CSRF token changed between requests"


# ── Discord OAuth state ──────────────────────────────────────────────────────
# SessionCodec.encode wrote only pid and csrf, so oauth_state was dropped on the
# way to the browser. The callback compared state against it, so the comparison
# always failed and every Discord sign-in landed on "that link expired".


def test_oauth_state_survives_the_cookie_round_trip():
    from app.web.session import Session, SessionCodec

    codec = SessionCodec("secret")
    session = Session(player_id=uuid.uuid4(), csrf_token="t", oauth_state="STATE123")

    assert codec.decode(codec.encode(session)).oauth_state == "STATE123"


def test_a_session_with_no_oauth_state_round_trips_as_none():
    from app.web.session import Session, SessionCodec

    codec = SessionCodec("secret")
    assert codec.decode(codec.encode(Session())).oauth_state is None


async def test_discord_sign_in_redirects_and_stores_state(client, monkeypatch):
    """The stored state must come back to the callback, or sign-in cannot work."""
    from app.web.session import COOKIE

    resp = await client.get("/auth/discord")
    assert resp.status_code in (302, 303, 307), resp.status_code
    assert "state=" in resp.headers["location"]

    # The state must be in the cookie we hand back, or the callback check can
    # never pass and Discord sign-in is dead.
    jar = client.cookies.get(COOKIE)
    assert jar, "no session cookie was issued"

    expected = resp.headers["location"].split("state=")[1].split("&")[0]
    session = client._transport.app.state.codec.decode(jar)
    assert session.oauth_state == expected, (
        "the state in the redirect is not the state in the cookie"
    )


# ── Rejoin tokens ────────────────────────────────────────────────────────────
# The token lived in a single player.rejoin_hash column, so joining a second
# league overwrote the first league's only recovery link.


async def test_rejoin_tokens_are_scoped_per_league(client):
    from app.models import RejoinToken

    await sign_in(client, "Admin")
    first = await make_league(client, name="League One")
    second = await make_league(client, name="League Two")

    guest = sibling(client)
    await post(guest, "/login", {"display_name": "Sam", "next": "/me"})
    token_one = (
        (await post(guest, f"/join/{first}", csrf_from=f"/join/{first}"))
        .headers["location"]
        .split("token=")[1]
    )
    await post(guest, f"/join/{second}", csrf_from=f"/join/{second}")

    app = client._transport.app
    async with app.state.db_factory() as db:
        rows = (await db.scalars(select(RejoinToken))).all()
    assert len(rows) == 2, "one token per league is expected, not one per player"

    # The first league's link still works after joining the second.
    fresh = sibling(client)
    assert (await fresh.get(f"/rejoin/{token_one}")).status_code == 303
    assert "League One" in (await fresh.get("/me")).text

    await guest.aclose()
    await fresh.aclose()


async def test_an_expired_rejoin_token_is_refused(client):
    from datetime import UTC, datetime, timedelta

    from app.models import RejoinToken

    await sign_in(client, "Admin")
    code = await make_league(client)

    guest = sibling(client)
    await post(guest, "/login", {"display_name": "Sam", "next": "/me"})
    token = (
        (await post(guest, f"/join/{code}", csrf_from=f"/join/{code}"))
        .headers["location"]
        .split("token=")[1]
    )

    app = client._transport.app
    async with app.state.db_factory() as db:
        await db.execute(
            update(RejoinToken).values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await db.commit()

    resp = await sibling(client).get(f"/rejoin/{token}")
    assert resp.status_code == 400, "an expired link must not restore a session"

    await guest.aclose()


# ── Joining state ────────────────────────────────────────────────────────────
# join.html hid the button once a draft started, but the POST had no such check,
# so a direct POST added a team mid-draft. That team has no slot in the pick
# order, which breaks the round/pick counters the hub displays.


@pytest.mark.parametrize(
    ("advance", "action"),
    [("drafting", "start-draft"), ("active", None), ("archived", None)],
)
async def test_you_cannot_join_a_closed_league(client, advance, action):
    await sign_in(client, "Admin")
    code = await make_league(client)

    if action:
        await post(client, f"/l/{code}/admin/action", {"action": "open-signup"})
        await add_players(client, ["Sam", "Priya"], code)
        await post(client, f"/l/{code}/admin/action", {"action": "close-signup"})
        await post(client, f"/l/{code}/admin/action", {"action": action})
    else:
        # Force the state directly rather than playing a whole season.
        from app.models import League

        app = client._transport.app
        async with app.state.db_factory() as db:
            await db.execute(update(League).where(League.code == code).values(state=advance))
            await db.commit()

    outsider = sibling(client)
    await post(outsider, "/login", {"display_name": "Latecomer", "next": "/me"})
    resp = await post(outsider, f"/join/{code}", csrf_from=f"/join/{code}")

    assert resp.status_code == 303
    assert "signup-closed" in resp.headers["location"], (
        f"a player joined a league in state {advance!r}"
    )

    await outsider.aclose()


# ── Seeding an unavailable season ────────────────────────────────────────────
# The season dropdown offered 2027, and ensure_season_seeded raised an
# unhandled ProviderError for it, so every signup for a season without published
# drivers was a 500 — a failure the UI itself invited.


async def test_signup_reports_an_unavailable_season_instead_of_500(client, monkeypatch):
    from app.provider.base import ProviderError
    from app.provider.jolpica import JolpicaProvider

    async def no_drivers(self, season: int):
        raise ProviderError(f"No drivers published for {season}")

    monkeypatch.setattr(JolpicaProvider, "drivers", no_drivers)

    resp = await post(
        client,
        "/signup",
        {
            "name": "Future League",
            "season": "2030",
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Pioneer",
        },
        csrf_from="/signup",
    )

    assert resp.status_code == 400, "an unavailable season must not be a 500"
    assert "2030" not in resp.text or "season" in resp.text.lower()
    assert "Internal Server Error" not in resp.text


async def test_a_failed_signup_leaves_no_orphan_player(client, monkeypatch):
    """Signup must be atomic.

    ensure_season_seeded used to commit whatever session it was handed, so a
    failure after it left a durable player row that the middleware's rollback
    could not undo.
    """
    from app.models import Player
    from app.provider.base import ProviderError
    from app.provider.jolpica import JolpicaProvider

    await sign_in(client, "Admin")
    await make_league(client)

    async def no_drivers(self, season: int):
        raise ProviderError("upstream down")

    monkeypatch.setattr(JolpicaProvider, "drivers", no_drivers)

    resp = await post(
        client,
        "/signup",
        {
            "name": "Doomed League",
            "season": "2031",
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Doomed",
        },
        csrf_from="/signup",
    )
    assert resp.status_code == 400

    app = client._transport.app
    async with app.state.db_factory() as db:
        names = [p.display_name for p in (await db.scalars(select(Player))).all()]
    assert "Doomed" not in names, "a failed signup left a player row behind"


# ── Draft grid ───────────────────────────────────────────────────────────────
# start-draft used `count or 22`, so a season with no drivers silently produced
# a 21-pick draft that could never be satisfied: every pick raised "Unknown
# driver" and every expiry found an empty pool.


async def test_a_draft_will_not_start_against_an_empty_season(client, monkeypatch):
    from app.models import League
    from app.provider.base import ProviderError
    from app.provider.jolpica import JolpicaProvider

    await sign_in(client, "Admin")
    code = await make_league(client)

    await post(client, f"/l/{code}/admin/action", {"action": "open-signup"})
    await add_players(client, ["Sam", "Priya"], code)
    await post(client, f"/l/{code}/admin/action", {"action": "close-signup"})

    # Move to a season that has no drivers, as reset does.
    app = client._transport.app
    async with app.state.db_factory() as db:
        await db.execute(update(League).where(League.code == code).values(season_year=2099))
        await db.commit()

    async def no_drivers(self, season: int):
        raise ProviderError("No drivers published")

    monkeypatch.setattr(JolpicaProvider, "drivers", no_drivers)

    resp = await post(client, f"/l/{code}/admin/action", {"action": "start-draft"})
    assert "error=no-drivers" in resp.headers["location"], (
        "a draft was started against an empty season instead of refusing"
    )


# ── Archive idempotency ──────────────────────────────────────────────────────
# season_archive is unique on (league_id, season_year) and _archive inserted
# unconditionally, so a second click raised an unhandled IntegrityError — while
# the module's own docstring promised actions were idempotent.


async def test_archiving_twice_is_not_an_error(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    first = await post(client, f"/l/{code}/admin/action", {"action": "archive"})
    assert first.status_code == 303
    assert "error=" not in first.headers["location"]

    second = await post(client, f"/l/{code}/admin/action", {"action": "archive"})
    assert second.status_code == 303, "a double submit produced a server error"
    assert "error=" not in second.headers["location"]


# ── Template context ─────────────────────────────────────────────────────────


async def test_the_draft_hub_names_the_player_on_the_clock(client):
    """It printed the raw team id because names_lookup was never passed."""
    from tests.test_draft_flow import on_the_clock, start_draft

    await sign_in(client, "Admin")
    code = await make_league(client)
    players = await start_draft(client, code, ["Sam", "Priya"])

    on_clock_name, _ = await on_the_clock(players, code)

    # Read the hub as somebody who is NOT on the clock: the player on the clock
    # is told "You are on the clock" instead.
    watcher = next(c for name, c in players.items() if name != on_clock_name)
    body = (await watcher.get(f"/l/{code}/draft")).text

    assert "Waiting on" in body, "the hub did not say who it is waiting on"
    waiting = body.split("Waiting on")[1].split("<")[0].strip()
    assert waiting == on_clock_name, (
        f"the hub is showing {waiting!r} instead of the name {on_clock_name!r}"
    )

    await close_all(players)


# ── Resource leaks ───────────────────────────────────────────────────────────


async def test_notifiers_share_one_http_pool(client):
    """A fresh httpx.AsyncClient per notifier meant a new socket pool on every
    draft page load, never closed."""
    from app.discord.client import WebhookClient

    first = WebhookClient("1", "token")
    second = WebhookClient("1", "token")
    assert first._client is second._client, "each webhook built its own pool"


async def test_the_shared_pool_is_disposed_on_shutdown():
    from app.discord.client import close_shared_client, shared_client

    client = shared_client()
    assert shared_client() is client, "the pool is not shared"
    await close_shared_client()
    assert shared_client() is not client, "the pool was not disposed"


# ── Module aliases ───────────────────────────────────────────────────────────


def test_db_session_alias_does_not_shadow_annotated():
    """`DbSession = Annotated = Depends(get_db)` rebound the module-level name
    Annotated to a Depends instance, so any `db: DbSession` annotation in this
    module would silently have resolved to Depends rather than Annotated."""
    from fastapi.params import Depends as DependsParam

    from app.web import deps

    assert isinstance(deps.DbSession, DependsParam)
    assert not hasattr(deps, "Annotated"), (
        "deps.Annotated is rebound; the chained assignment is back"
    )
