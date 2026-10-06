"""Test fixtures for the web layer.

The web layer is exercised against a real Postgres rather than mocks. Every bug
found while building it — a lost commit, a stale session, an undefined template
variable — was invisible to unit tests with a fake repository, and most of them
are invisible to anything that isn't a real request through the real app.
"""

from __future__ import annotations

import os
import pathlib
import re

import httpx
import pytest

# Must be set before app.config is imported anywhere.
_TEST_DATABASE = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:55440/f1_test",
)
os.environ.setdefault("DATABASE_URL", _TEST_DATABASE)
os.environ.setdefault("DISCORD_CLIENT_ID", "test")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test")
os.environ.setdefault("SESSION_SECRET", "test-only-not-a-real-secret")
os.environ.setdefault("APP_URL", "http://testserver")

_SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _tables() -> list[str]:
    """Every table in the schema, so a new one cannot leak between tests.

    Derived from schema.sql rather than hand-listed. A hand-maintained list
    silently stops truncating the day someone adds a table, and the failure
    looks like a flaky test rather than leaked state.
    """
    found = re.findall(
        r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)",
        _SCHEMA.read_text(encoding="utf-8"),
    )
    assert len(found) >= 12, f"only found {len(found)} tables in schema.sql: {found}"
    return sorted(found)


def _raw_dsn() -> str:
    """The DSN with the SQLAlchemy driver suffix stripped.

    asyncpg parses URLs itself and rejects ``postgresql+asyncpg://``.
    """
    return _TEST_DATABASE.replace("postgresql+asyncpg://", "postgresql://")


def _admin_dsn() -> str:
    """The same server, pointed at the default database.

    Needed to create or drop the test database. asyncpg cannot parse the
    unix-socket shorthand with an empty path, so name the database explicitly.
    """
    # Strip the SQLAlchemy driver suffix; asyncpg wants a plain scheme.
    base, _, _ = _raw_dsn().rpartition("/")
    return f"{base}/postgres"


@pytest.fixture(scope="session")
def database() -> str:
    """Create the schema once per session."""
    import asyncio

    import asyncpg

    async def setup():
        conn = await asyncpg.connect(_admin_dsn())
        try:
            await conn.execute("DROP DATABASE IF EXISTS f1_test")
            await conn.execute("CREATE DATABASE f1_test")
        finally:
            await conn.close()

        conn = await asyncpg.connect(_raw_dsn())
        try:
            await conn.execute(_SCHEMA.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    asyncio.run(setup())
    return _TEST_DATABASE


@pytest.fixture
def clean_database(database: str):
    """Truncate every table so tests cannot leak state into each other."""
    import asyncio

    import asyncpg

    async def wipe():
        conn = await asyncpg.connect(_raw_dsn())
        try:
            await conn.execute(f"TRUNCATE {', '.join(_tables())} RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(wipe())
    yield database


@pytest.fixture(autouse=True)
def stub_provider(monkeypatch):
    """Never hit the network from a web test.

    League creation seeds the season grid, which would otherwise make every
    signup test wait on Jolpica. The simulator job is what verifies the real
    provider end to end.
    """
    from app.provider.base import Driver
    from app.provider.jolpica import JolpicaProvider

    async def fake_drivers(self, season: int) -> list[Driver]:
        return [
            Driver(code=f"D{i:02d}", name=f"Driver {i}", constructor="Test Racing")
            for i in range(20)
        ]

    monkeypatch.setattr(JolpicaProvider, "drivers", fake_drivers)


@pytest.fixture
def app(clean_database: str):
    """A fresh application bound to the test database."""
    from app.web.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    """In-process HTTP client. Follows nothing so redirects stay assertable."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    )


async def sign_in(client, name: str) -> None:
    """Establish a guest session."""
    resp = await client.post("/login", data={"display_name": name, "next": "/me"})
    assert resp.status_code == 303, resp.text


async def make_league(client, *, name: str = "Test League", season: int = 2025) -> str:
    """Create a league as the signed-in user. Returns its code."""
    resp = await client.post(
        "/signup",
        data={
            "name": name,
            "season": str(season),
            "pick_deadline": "600",
            "team_size": "",
            "display_name": "Admin",
            "discord_id": "",
        },
    )
    assert resp.status_code == 303, resp.text
    return resp.headers["location"].split("/")[2]


def sibling(client) -> httpx.AsyncClient:
    """A client sharing the app but with its own cookie jar."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app),
        base_url="http://testserver",
        follow_redirects=False,
    )


async def add_players(client, names: list[str], code: str) -> dict[str, httpx.AsyncClient]:
    """Sign each name in and join, returning a live client per player.

    Live clients rather than captured cookie strings. Round-tripping a session
    through a hand-rolled header is lossy and produced failures that looked like
    application bugs.
    """
    out: dict[str, httpx.AsyncClient] = {}
    for name in names:
        other = sibling(client)
        await other.post("/login", data={"display_name": name, "next": "/me"})
        await other.post(f"/join/{code}")
        out[name] = other
    return out


async def close_all(clients: dict) -> None:
    for c in clients.values():
        await c.aclose()
