"""One-shot entrypoints: cron, migrations, seeding.

Railway runs the cron service on a schedule and **skips the run if the previous
one is still going**. That makes a non-exiting scorer the most likely way for a
league to silently stop being scored, so the rules here are strict:

* The process must exit. No daemon threads, no idle loop.
* Every connection is closed before exit.
* Progress is recorded in ``job_lock`` so a hung or failed run is visible
  instead of silent.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

log = logging.getLogger("cli")

#: A cron run still going after this long is treated as hung. Railway gives no
#: warning when it skips a run, so we surface it ourselves.
HUNG_AFTER_SECONDS = 15 * 60


def utcnow() -> datetime:
    return datetime.now(UTC)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


async def claim_lock(engine, name: str) -> bool:
    """Take the lock, refusing if a previous run never completed."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT started_at, completed_at FROM job_lock WHERE name = :n"),
                {"n": name},
            )
        ).fetchone()

        if row is not None and row.completed_at is None:
            age = (utcnow() - row.started_at).total_seconds()
            if age > HUNG_AFTER_SECONDS:
                log.warning("job_lock %r has been running for %.0fs — reclaiming", name, age)
            else:
                log.info("job_lock %r still held (%.0fs old) — skipping", name, age)
                return False

        await conn.execute(
            text(
                """
                INSERT INTO job_lock (name, started_at, heartbeat_at, completed_at, error)
                VALUES (:n, now(), now(), NULL, NULL)
                ON CONFLICT (name) DO UPDATE SET
                    started_at = now(), heartbeat_at = now(),
                    completed_at = NULL, error = NULL
                """
            ),
            {"n": name},
        )
    return True


async def release_lock(engine, name: str, error: str | None = None) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE job_lock
                SET completed_at = now(), heartbeat_at = now(), error = :e
                WHERE name = :n
                """
            ),
            {"e": error, "n": name},
        )


async def heartbeat(engine, name: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE job_lock SET heartbeat_at = now() WHERE name = :n"), {"n": name}
        )


async def run_score() -> int:
    """Score every recently-finished event and post results to Discord.

    Exits 0 on success, 1 on failure, 2 when skipped because a run was in
    flight. Non-zero on error is what makes a failure visible in Railway.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    if not await claim_lock(engine, settings.cron_lock_name):
        await engine.dispose()
        return 2

    try:
        from app.services.scoring import score_recent_events

        scored = await score_recent_events(engine, window_hours=settings.post_race_window_hours)
        log.info("cron: scored %d event(s)", scored)
        await release_lock(engine, settings.cron_lock_name)
        return 0
    except Exception as exc:
        log.exception("cron: scoring failed")
        await release_lock(engine, settings.cron_lock_name, error=str(exc)[:2000])
        return 1
    finally:
        # Must dispose or the process never exits and Railway skips every
        # subsequent run.
        await engine.dispose()


async def run_migrate() -> int:
    """Apply migrations. Idempotent — safe to run on every container start."""
    from app.db import migrate

    await migrate.upgrade()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f1-fantasy")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("score", help="score finished events and post to Discord")
    sub.add_parser("migrate", help="apply database migrations")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    runners = {"score": run_score, "migrate": run_migrate}
    try:
        return asyncio.run(runners[args.command]())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
