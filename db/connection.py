from __future__ import annotations

import asyncio
import pathlib
from typing import Optional

import aiosqlite

import config

_connection: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


async def get_db() -> aiosqlite.Connection:
    """Return the shared aiosqlite connection, initialising it if necessary.

    This is intentionally a module-level singleton.  All cogs and utilities
    should obtain the connection through this function rather than opening
    their own connections.
    """
    global _connection
    async with _lock:
        if _connection is None:
            _connection = await aiosqlite.connect(config.DB_PATH)
            _connection.row_factory = aiosqlite.Row
            await _connection.execute("PRAGMA journal_mode=WAL")
            await _connection.execute("PRAGMA foreign_keys=ON")
        return _connection


async def init_db() -> None:
    """Read schema.sql and execute every statement against the database.

    Safe to call on every startup — all CREATE TABLE statements use
    IF NOT EXISTS.
    """
    db = await get_db()
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    # executescript requires a plain connection; use executemany-style loop
    # so we stay within the async context and respect the existing connection.
    statements = [
        stmt.strip()
        for stmt in schema_sql.split(";")
        if stmt.strip() and not stmt.strip().startswith("--")
    ]
    async with db.cursor() as cur:
        for statement in statements:
            await cur.execute(statement)
    await db.commit()


async def close_db() -> None:
    """Close the shared connection.  Call this on bot shutdown."""
    global _connection
    async with _lock:
        if _connection is not None:
            await _connection.close()
            _connection = None
