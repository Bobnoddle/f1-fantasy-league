"""Simulation entrypoint.

    python -m app.sim --season 2025 --players 6 --through 10
    python -m app.sim --season 2024 --players 8 --discord --pick-deadline 300

Runs against a real database and real Jolpica data. Nothing is stubbed except
the players themselves, so a successful run is evidence the schema constraints,
turn ordering, timeout handling, and scoring all hold together.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import ConfigError, get_settings
from app.provider.jolpica import JolpicaProvider
from app.sim.runner import SimConfig, Simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.sim",
        description="Simulate a full F1 Fantasy league against real data.",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=2025,
        help="Season to simulate. A finished season has full data (default: 2025)",
    )
    parser.add_argument("--players", type=int, default=6, help="Number of simulated players")
    parser.add_argument(
        "--through",
        type=int,
        default=None,
        help="Stop after this round. Omit to play the whole season",
    )
    parser.add_argument("--pick-deadline", type=int, default=600, help="Pick timer, seconds")
    parser.add_argument("--team-size", type=int, default=None, help="Override auto team size")
    parser.add_argument("--code", default="sim", help="League code")
    parser.add_argument("--name", default="Simulated League", help="League name")
    parser.add_argument("--seed", type=int, default=7, help="Seed for agent behaviour")
    parser.add_argument(
        "--speed",
        type=float,
        default=3600.0,
        help="Time compression. 3600 = 1 real second per virtual hour",
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="Post to the league's Discord webhook instead of the console",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-step output")
    parser.add_argument("--log-level", default="WARNING")
    return parser


async def run(args: argparse.Namespace) -> int:
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
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _reset_league(session, config.league_code)

            async with JolpicaProvider(retries=3) as provider:
                sim = Simulator(session, provider, config)
                league = await sim.setup(args.season)
                await sim.run_draft(league)
                await sim.run_season(league, through_round=args.through)
                summary = await sim.summarise(league)
            print(summary)

            result = sim.report
            ok = result.picks > 0 and (result.races_scored > 0 or args.through is None)
            return 0 if ok else 1
    finally:
        await engine.dispose()


async def _reset_league(session, code: str) -> None:
    """Drop any previous run with this code so the sim is repeatable."""
    from sqlalchemy import delete

    from app.models import League

    existing = (await session.scalars(select_code(code))).all()
    for league_id in existing:
        await session.execute(delete(League).where(League.id == league_id))
    await session.commit()


def select_code(code: str):
    from sqlalchemy import select

    from app.models import League

    return select(League.id).where(League.code == code)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    try:
        get_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
