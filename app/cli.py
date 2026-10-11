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
import pathlib
import sys
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import ConfigError, get_settings
from app.models import League
from app.provider.jolpica import JolpicaProvider

log = logging.getLogger("cli")

_SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"

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
    """Apply db/schema.sql. Idempotent — safe on every container start.

    Bootstrap only. The schema uses CREATE TABLE IF NOT EXISTS, which handles
    adding new tables but will not alter an existing one. Once a column needs to
    change in place, this needs Alembic migrations rather than an edited
    bootstrap file.
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            # asyncpg refuses multi-statement prepared statements, and splitting
            # the file on ";" would be fragile. The raw driver connection
            # accepts a multi-statement script.
            raw = (await conn.get_raw_connection()).driver_connection
            await raw.execute(sql)
        log.info("migrate: schema applied")
        return 0
    except Exception:
        log.exception("migrate: failed")
        return 1
    finally:
        await engine.dispose()


async def run_simulate(args: argparse.Namespace) -> int:
    """Play a simulated league end to end against real data.

    Exits non-zero if the draft produced nothing or no race scored, so it is
    usable as a CI gate.
    """
    from app.sim.runner import SimConfig, Simulator

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    config = SimConfig(
        league_code=args.code,
        league_name=args.name,
        players=args.players,
        pick_deadline=args.pick_deadline,
        team_size=args.team_size,
        seed=args.seed,
        time_compression=args.speed,
        discord=args.discord,
        verbose=not args.quiet,
        attach=getattr(args, "attach", None),
        human_grace=getattr(args, "human_grace", 900),
        signup_only=getattr(args, "signup_only", False),
    )

    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if config.attach:
                # The league belongs to a human. Never delete it — that is the
                # whole point of attaching.
                pass
            else:
                existing = await session.scalars(
                    select(League.id).where(League.code == config.league_code)
                )
                for league_id in existing.all():
                    await session.execute(delete(League).where(League.id == league_id))
                await session.commit()

            async with JolpicaProvider(retries=settings.fetch_retries) as provider:
                sim = Simulator(session, provider, config)
                if config.attach:
                    league = await sim.attach(config.attach, args.season)
                else:
                    league = await sim.setup(args.season)

                # Attach with --signup-only fills the roster and stops. The
                # admin opens and closes signup, and starts the draft, from the
                # panel — those are their buttons, not this command's.
                if config.signup_only:
                    # attach() has already said how many bots it added. Printing
                    # a standings table here would show only zeros — there is no
                    # draft and no scored race yet.
                    await session.commit()
                    return 0

                await sim.run_draft(league)
                await sim.run_season(league, through_round=args.through)

                # Must be explicit. AsyncSession.__aexit__ closes the session and
                # discards anything merely flushed, and run_season's final
                # transition to "active" is exactly that. Without this the league
                # stayed in "drafting" while carrying a full season of results.
                await session.commit()

                print(await sim.summarise(league))

            result = sim.report
            ok = result.picks > 0 and (result.races_scored > 0 or args.through is None)
            if not ok:
                log.error(
                    "simulate: picks=%d races=%d — expected a completed draft",
                    result.picks,
                    result.races_scored,
                )
            return 0 if ok else 1
    except Exception:
        log.exception("simulate: failed")
        return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f1-fantasy")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("score", help="score finished events and post to Discord")
    sub.add_parser("migrate", help="apply the database schema")

    sim = sub.add_parser("simulate", help="play a simulated league against real data")
    sim.add_argument("--season", type=int, default=2025, help="season to simulate")
    sim.add_argument("--players", type=int, default=6)
    sim.add_argument("--through", type=int, default=None, help="stop after this round")
    sim.add_argument("--pick-deadline", type=int, default=600)
    sim.add_argument("--team-size", type=int, default=None)
    sim.add_argument("--code", default="sim")
    sim.add_argument("--name", default="Simulated League")
    sim.add_argument("--seed", type=int, default=7)
    sim.add_argument("--speed", type=float, default=1.0, help="time compression")
    sim.add_argument("--discord", action="store_true", help="post to the league webhook")
    sim.add_argument(
        "--attach",
        metavar="CODE",
        default=None,
        help=(
            "Add bots to a league you already created and joined, instead of "
            "making a new one. Your team is left to you: the draft waits at "
            "your turn, and picks for you if you walk away."
        ),
    )
    sim.add_argument(
        "--human-grace",
        type=int,
        default=900,
        help="seconds to wait for your pick before it is settled for you",
    )
    sim.add_argument(
        "--signup-only",
        action="store_true",
        help=(
            "Add the bots and stop, leaving the draft untouched. Use this to "
            "fill signup from the panel afterwards."
        ),
    )
    sim.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    if args.command == "simulate":
        return asyncio.run(run_simulate(args))

    runners = {"score": run_score, "migrate": run_migrate}
    try:
        return asyncio.run(runners[args.command]())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
