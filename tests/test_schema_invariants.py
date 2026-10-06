"""The storage-layer guard, tested directly.

Two players racing for the same driver must resolve at the database, not in an
application code path someone can forget. This is that guarantee, checked with
real Postgres rather than by reading the schema.
"""

from __future__ import annotations

import pytest


async def test_a_driver_can_only_be_owned_once(client):
    """roster's primary key is (league_id, driver_id).

    Duplicate ownership is what makes a league unscoreable, so the invariant is
    asserted against a live database rather than trusted.
    """
    import asyncpg

    from tests.conftest import _raw_dsn, make_league, sign_in

    await sign_in(client, "Admin")
    code = await make_league(client)

    conn = await asyncpg.connect(_raw_dsn())
    try:
        inserted = await conn.fetch(
            """
            INSERT INTO roster (league_id, team_id, driver_id, pick_number)
            SELECT l.id, t.id, d.id, 0
            FROM league l
            JOIN team t    ON t.league_id = l.id
            JOIN driver d  ON d.season_year = l.season_year
            WHERE l.code = $1
            LIMIT 1
            RETURNING league_id, team_id, driver_id
            """,
            code,
        )
        assert inserted, "no row inserted, so nothing to duplicate"

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO roster (league_id, team_id, driver_id, pick_number)
                VALUES ($1, $2, $3, 1)
                """,
                inserted[0]["league_id"],
                inserted[0]["team_id"],
                inserted[0]["driver_id"],
            )
    finally:
        await conn.close()


async def test_a_driver_may_be_owned_in_different_leagues(client):
    """The guard is per league, not global."""
    import asyncpg

    from tests.conftest import _raw_dsn, make_league, sign_in

    await sign_in(client, "Admin")
    first = await make_league(client, name="League One")
    second = await make_league(client, name="League Two")

    conn = await asyncpg.connect(_raw_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO roster (league_id, team_id, driver_id, pick_number)
            SELECT l.id, t.id, d.id, 0
            FROM league l
            JOIN team t   ON t.league_id = l.id
            JOIN driver d ON d.season_year = l.season_year
            WHERE l.code = $1
            LIMIT 1
            RETURNING driver_id
            """,
            first,
        )
        assert row is not None

        await conn.execute(
            """
            INSERT INTO roster (league_id, team_id, driver_id, pick_number)
            SELECT l.id, t.id, $2, 0
            FROM league l
            JOIN team t ON t.league_id = l.id
            WHERE l.code = $1
            LIMIT 1
            """,
            second,
            row["driver_id"],
        )
    finally:
        await conn.close()
