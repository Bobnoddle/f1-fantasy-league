"""Fixtures for the web tests.

The web layer is exercised against a real Postgres rather than mocks. Every bug
found while building it — a lost commit, a stale session, an undefined template
variable — was invisible to unit tests with a fake repository, and most are
invisible to anything that is not a real request through the real app.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from tests.helpers import SCHEMA, TEST_DATABASE, admin_dsn, database_name, raw_dsn, tables

# Must be set before app.config is imported anywhere.
os.environ.setdefault("DATABASE_URL", TEST_DATABASE)
os.environ.setdefault("DISCORD_CLIENT_ID", "test")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test")
os.environ.setdefault("SESSION_SECRET", "test-only-not-a-real-secret")
os.environ.setdefault("APP_URL", "http://testserver")


@pytest.fixture(scope="session")
def database() -> str:
    """Create the test database and apply the schema, once per session."""
    import asyncpg

    async def setup():
        name = database_name()
        conn = await asyncpg.connect(admin_dsn())
        try:
            # Drop first: a stale database from an aborted run would otherwise
            # carry a schema that no longer matches db/schema.sql.
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

        conn = await asyncpg.connect(raw_dsn())
        try:
            await conn.execute(SCHEMA.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    asyncio.run(setup())
    return TEST_DATABASE


@pytest.fixture
def clean_database(database: str):
    """Truncate every table so tests cannot leak state into each other."""
    import asyncpg

    async def wipe():
        conn = await asyncpg.connect(raw_dsn())
        try:
            await conn.execute(f"TRUNCATE {', '.join(tables())} RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(wipe())
    yield database


@pytest.fixture(autouse=True)
def stub_provider(monkeypatch):
    """Never hit the network from a web test.

    League creation seeds the season grid, which would otherwise make every
    signup test wait on Jolpica. The simulate CI job is what verifies the real
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
    """In-process HTTP client. Follows nothing, so redirects stay assertable."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    )
