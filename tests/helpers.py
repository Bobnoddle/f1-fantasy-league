"""Shared helpers for the web tests.

Kept separate from conftest.py on purpose. conftest holds fixtures; this holds
the functions tests call. Importing these via ``from tests.helpers import ...``
works under a bare ``pytest`` invocation, whereas importing them out of
conftest.py did not — conftest is not an importable module by that path.
"""

from __future__ import annotations

import os
import pathlib
import re

import httpx

#: Must match what CI sets, or tests silently run against a developer's local
#: database instead of the throwaway one.
TEST_DATABASE = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:55440/f1_test",
)

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def tables() -> list[str]:
    """Every table in the schema, so a new one cannot leak between tests.

    Derived from schema.sql rather than hand-listed. A hand-maintained list
    silently stops truncating the day someone adds a table, and the failure
    then looks like a flaky test rather than leaked state.
    """
    found = re.findall(
        r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)",
        SCHEMA.read_text(encoding="utf-8"),
    )
    assert len(found) >= 12, f"only found {len(found)} tables in schema.sql: {found}"
    return sorted(found)


def raw_dsn() -> str:
    """The test DSN with the SQLAlchemy driver suffix stripped.

    asyncpg parses URLs itself and rejects ``postgresql+asyncpg://``.
    """
    return TEST_DATABASE.replace("postgresql+asyncpg://", "postgresql://")


def admin_dsn() -> str:
    """The same server, pointed at the default database.

    Needed to create or drop the test database, which cannot happen from inside
    the database being dropped.
    """
    base, _, _ = raw_dsn().rpartition("/")
    return f"{base}/postgres"


def database_name() -> str:
    """The test database's own name, so drop/create use it rather than a guess."""
    return raw_dsn().rpartition("/")[2]


# ── Driving the app ──────────────────────────────────────────────────────────


def csrf_of(html: str) -> str:
    """Pull the CSRF token out of a rendered form.

    Every unsafe request is now checked centrally in the middleware, so a POST
    without this is rejected with a 403 — which reads exactly like an application
    bug. Sent as a header rather than a form field so helpers do not have to
    model which form posted what.
    """
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "no csrf_token in the page; the CSRF field is missing"
    return match.group(1)


async def token_for(client, path: str) -> str:
    """Fetch a page and return its CSRF token."""
    resp = await client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    return csrf_of(resp.text)


async def post(client, path: str, data: dict | None = None, *, csrf_from: str | None = None):
    """POST with a valid CSRF token attached.

    ``csrf_from`` names the page to read the token from; it defaults to
    ``/login``, which is reachable while signed out. Every helper goes through
    here so the tests exercise the same guard the browser does.
    """
    token = await token_for(client, csrf_from or "/login")
    payload = {"csrf_token": token, **(data or {})}
    return await client.post(path, data=payload)


async def sign_in(client, name: str) -> None:
    """Establish a guest session."""
    resp = await post(client, "/login", {"display_name": name, "next": "/me"})
    assert resp.status_code == 303, resp.text


async def make_league(client, *, name: str = "Test League", season: int = 2025) -> str:
    """Create a league as the signed-in user. Returns its code."""
    resp = await post(
        client,
        "/signup",
        {
            "name": name,
            "season": str(season),
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Admin",
        },
    )
    assert resp.status_code == 303, resp.text
    return resp.headers["location"].split("/")[2]


def sibling(client) -> httpx.AsyncClient:
    """A client sharing the app under test but with its own cookie jar."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app),
        base_url="http://testserver",
        follow_redirects=False,
    )


async def add_players(client, names: list[str], code: str) -> dict[str, httpx.AsyncClient]:
    """Sign each name in and join, returning a live client per player.

    Live clients rather than captured cookie strings. Round-tripping a session
    through a hand-built header is lossy, and the resulting failures read like
    application bugs rather than test-helper bugs.
    """
    out: dict[str, httpx.AsyncClient] = {}
    for name in names:
        other = sibling(client)
        await post(other, "/login", {"display_name": name, "next": "/me"})
        await post(other, f"/join/{code}", csrf_from=f"/join/{code}")
        out[name] = other
    return out


async def close_all(clients: dict) -> None:
    for c in clients.values():
        await c.aclose()
