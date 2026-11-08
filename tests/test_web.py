"""Web layer tests.

Every test here corresponds to something that actually broke during the build,
or to a rule the design depends on. The list is not aspirational — the first
eight were real defects found by hand, and each would have shipped.

The database is real Postgres. Mocks would have hidden the lost commit, the
stale identity map, and the Row/ORM confusion that each cost real time.
"""

from __future__ import annotations

import uuid

from tests.helpers import make_league, post, sign_in

# ── Public reads need no login ───────────────────────────────────────────────


async def test_landing_renders_anonymously(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Start a league" in resp.text


async def test_rules_page_renders(client):
    resp = await client.get("/rules")
    assert resp.status_code == 200
    assert "RACE FINISH" in resp.text.upper()


async def test_pricing_offers_both_tiers(client):
    resp = await client.get("/pricing")
    assert resp.status_code == 200
    assert "Self-host" in resp.text
    assert "Hosted" in resp.text


async def test_health_needs_no_database_round_trip(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_checks_the_database(client):
    assert (await client.get("/health/ready")).status_code == 200


async def test_unknown_league_is_a_404(client):
    resp = await client.get("/l/does-not-exist")
    assert resp.status_code == 404
    assert "No league" in resp.text


async def test_standings_are_public(client):
    """A signed-out visitor must be able to read a league."""
    await sign_in(client, "Admin")
    code = await make_league(client)
    client.cookies.clear()

    resp = await client.get(f"/l/{code}")
    assert resp.status_code == 200


# ── Sign-in and league creation ─────────────────────────────────────────────


async def test_login_sets_a_session(client):
    await sign_in(client, "Dave")
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert "Dave" in resp.text


async def test_login_redirect_honours_next(client):
    resp = await post(client, "/login", {"display_name": "Dave", "next": "/l/whatever"})
    assert resp.headers["location"] == "/l/whatever"


async def test_login_refuses_an_offsite_redirect(client):
    """next is user-supplied, so it must never leave the site."""
    resp = await post(client, "/login", {"display_name": "Dave", "next": "https://evil.example/x"})
    assert resp.headers["location"] == "/me"


async def test_anonymous_dashboard_redirects_to_login(client):
    resp = await client.get("/me")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


async def test_logout_clears_the_session(client):
    await sign_in(client, "Dave")
    await post(client, "/logout")
    assert (await client.get("/me")).status_code == 303


# ── Signup persists and establishes the session ─────────────────────────────
# Regression: signup created the player but never set the session, so the
# creator was redirected to their own admin page and bounced off it as anonymous.


async def test_signup_creates_a_league(client):
    await sign_in(client, "Admin")
    code = await make_league(client, name="Barham GP")

    resp = await client.get(f"/l/{code}")
    assert resp.status_code == 200
    assert "Barham GP" in resp.text


async def test_signup_makes_the_creator_admin(client):
    """The whole point of the redirect after signup."""
    await sign_in(client, "Admin")
    code = await make_league(client)

    resp = await client.get(f"/l/{code}/admin")
    assert resp.status_code == 200


async def test_signup_code_is_slugged_and_unique(client):
    await sign_in(client, "Admin")
    first = await make_league(client, name="Barham GP")
    assert first == "barham-gp"

    other = httpx_client(client)
    await sign_in(other, "Someone Else")
    second = await make_league(other, name="Barham GP")
    assert second != first


def httpx_client(client):
    """A sibling client sharing the same app but with its own cookie jar."""
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app),
        base_url="http://testserver",
        follow_redirects=False,
    )


async def test_signup_seeds_the_season_grid(client):
    """A league with no drivers has nothing to draft.

    Discovered by hand: a web-created league started its draft against an empty
    pool because nothing had loaded the season.
    """
    from sqlalchemy import func, select

    from app.models import Driver

    await sign_in(client, "Admin")
    await make_league(client, season=2024)

    app = client._transport.app
    async with app.state.db_factory() as db:
        count = await db.scalar(select(func.count(Driver.id)).where(Driver.season_year == 2024))
        assert count == 20


# ── Joining ─────────────────────────────────────────────────────────────────


async def test_joining_adds_a_team(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    resp = await post(other, f"/join/{code}")
    assert resp.status_code == 303


async def test_joining_twice_is_not_an_error(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    await post(other, f"/join/{code}")
    resp = await post(other, f"/join/{code}")
    assert resp.status_code == 303

    resp = await client.get(f"/l/{code}/admin")
    assert "Sam" in resp.text


# ── Guest identity: the magic link ──────────────────────────────────────────
# Regression: guest sign-in always minted a new player, so a guest who cleared
# their cookie lost their team with no way back. The link is the only fix.


async def test_guest_receives_a_rejoin_link(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    resp = await post(other, f"/join/{code}")

    assert "/welcome/" in resp.headers["location"]
    token = resp.headers["location"].split("token=")[1]
    assert len(token) > 20


async def test_rejoin_restores_the_session_and_the_team(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    token = (await post(other, f"/join/{code}")).headers["location"].split("token=")[1]

    # A completely fresh session, as if the cookie were gone.
    fresh = httpx_client(client)
    assert (await fresh.get("/me")).status_code == 303

    assert (await fresh.get(f"/rejoin/{token}")).status_code == 303

    me = await fresh.get("/me")
    assert me.status_code == 200
    assert "Sam" in me.text

    # Still in the league they joined.
    standings = await fresh.get(f"/l/{code}")
    assert "Sam" in standings.text


async def test_rejoin_rejects_a_bad_token(client):
    resp = await client.get("/rejoin/not-a-real-token-at-all-really")
    assert resp.status_code == 400


async def test_rejoin_token_is_stored_hashed(client):
    """It is a bearer credential. A database leak must not hand over accounts."""
    from sqlalchemy import select

    from app.models import RejoinToken

    await sign_in(client, "Admin")
    code = await make_league(client)
    other = httpx_client(client)
    await sign_in(other, "Sam")
    token = (await post(other, f"/join/{code}")).headers["location"].split("token=")[1]

    app = client._transport.app
    async with app.state.db_factory() as db:
        rows = (await db.scalars(select(RejoinToken))).all()
        hashes = {r.token_hash for r in rows}
        assert hashes, "no rejoin token was stored"
        assert all(len(h) == 64 for h in hashes)  # sha256 hex
        assert all(token not in h for h in hashes)  # never the raw token


async def test_a_second_league_does_not_break_the_first_link(client):
    """Joining another league used to overwrite the only recovery link.

    The token lived in a single ``player.rejoin_hash`` column, so a guest in two
    leagues lost the first league's link permanently, with no route to a new one.
    """
    first = await make_league(client, name="League One")
    second = await make_league(client, name="League Two")

    other = httpx_client(client)
    await sign_in(other, "Sam")

    token_one = (
        (await post(other, f"/join/{first}", csrf_from=f"/join/{first}"))
        .headers["location"]
        .split("token=")[1]
    )
    await post(other, f"/join/{second}", csrf_from=f"/join/{second}")

    # A brand-new session, as if the cookie were gone.
    fresh = httpx_client(client)
    assert (
        await post(fresh, "/login", {"display_name": "Someone", "next": "/me"})
    ).status_code == 303
    fresh.cookies.clear()

    assert (await fresh.get(f"/rejoin/{token_one}")).status_code == 303

    me = await fresh.get("/me")
    assert "Sam" in me.text
    assert "League One" in me.text, "the first league's team was lost"

    await other.aclose()
    await fresh.aclose()


# ── Admin gating ────────────────────────────────────────────────────────────


async def test_admin_panel_rejects_non_admins(client):
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    resp = await other.get(f"/l/{code}/admin")
    assert resp.status_code == 403


async def test_admin_actions_reject_non_admins(client):
    """Gating the panel is not enough — the POST must be guarded too."""
    await sign_in(client, "Admin")
    code = await make_league(client)

    other = httpx_client(client)
    await sign_in(other, "Sam")
    resp = await post(other, f"/l/{code}/admin/action", {"action": "start-draft"})
    assert resp.status_code == 403


async def test_admin_action_rejects_unknown_actions(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    resp = await post(client, f"/l/{code}/admin/action", {"action": "launch-missiles"})
    assert resp.status_code == 303
    assert "unknown-action" in resp.headers["location"]


async def test_draft_cannot_start_with_one_player(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    resp = await post(client, f"/l/{code}/admin/action", {"action": "start-draft"})
    assert "need-2-players" in resp.headers["location"]


# ── Draft gating and picking ────────────────────────────────────────────────


async def test_standings_render_positions(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    resp = await client.get(f"/l/{code}")
    assert resp.status_code == 200
    assert "Admin" in resp.text


async def test_team_page_rejects_an_unknown_team(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    resp = await client.get(f"/l/{code}/team/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_team_page_rejects_a_malformed_id(client):
    await sign_in(client, "Admin")
    code = await make_league(client)
    resp = await client.get(f"/l/{code}/team/not-a-uuid")
    assert resp.status_code == 404


# ── Session codec ───────────────────────────────────────────────────────────


def test_session_roundtrips():
    from app.web.session import Session, SessionCodec

    codec = SessionCodec("secret")
    original = Session(player_id=uuid.uuid4(), csrf_token="abc")
    assert codec.decode(codec.encode(original)) == original


def test_a_tampered_cookie_becomes_anonymous():
    from app.web.session import SessionCodec

    codec = SessionCodec("secret")
    assert codec.decode("garbage.player_id=x").player_id is None
    assert codec.decode(None).player_id is None


def test_a_cookie_signed_with_another_secret_is_rejected():
    from app.web.session import Session, SessionCodec

    forged = SessionCodec("other-secret").encode(Session(player_id=uuid.uuid4(), csrf_token="t"))
    assert SessionCodec("secret").decode(forged).player_id is None


def test_csrf_rejects_a_missing_or_wrong_token():
    from app.web.session import Session

    session = Session(csrf_token="correct-token")
    assert session.verify_csrf("correct-token")
    assert not session.verify_csrf("wrong-token")
    assert not session.verify_csrf("")
    assert not session.verify_csrf(None)
