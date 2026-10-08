<div align="center">
  <img src="https://raw.githubusercontent.com/Bobnoddle/f1-fantasy-league/main/f1-fantasy.png" alt="F1 Fantasy League" width="200" />

  # F1 Fantasy League

  [![Python 3.12](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://www.python.org/downloads/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
  [![Postgres](https://img.shields.io/badge/PostgreSQL-16+-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
  [![License MIT](https://img.shields.io/badge/License-MIT-yellow?logo=open-source-initiative&logoColor=white)](LICENSE)
  [![CI](https://github.com/Bobnoddle/f1-fantasy-league/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Bobnoddle/f1-fantasy-league/actions/workflows/ci.yml)

</div>

A server-rendered web app for running F1 Fantasy leagues with snake drafts and
automated race scoring. Discord is an optional output channel — announcements go
to a webhook if you set one, and a league with no Discord at all is fully playable.

## Features

- **Snake draft** — one-tap driver picking, team sizes auto-scaled to the player count
- **Automatic race scoring** — pulls results from the Jolpica API and scores them
- **Full-field scoring** — every classified position earns points, including lapped cars
- **Public standings** — readable without signing in; share the link
- **Guest identity** — no account needed, a private magic link gets you back in
- **DB-driven pick timers** — a lapsed pick settles lazily, so a deploy can't interrupt a draft
- **Play against bots** — fill your own league with simulated players and race them

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # fill in DATABASE_URL, SESSION_SECRET and APP_URL
python -m app.cli migrate

uvicorn app.web.app:app --reload --port 8099
```

Open <http://127.0.0.1:8099>, create a league, and share the join link.

`SESSION_SECRET` must be a random string — `openssl rand -hex 32`. Discord
variables are optional; leaving them empty runs the app with no Discord
integration at all.

## Playing against bots

The simulator plays a whole league against real data. By default it builds its
own league from scratch:

```bash
python -m app.cli simulate --season 2025 --players 6 --through 5
```

To play one yourself, create a league in the browser first, join it, then attach
bots to **your** league:

```bash
python -m app.cli simulate --attach your-league-code --players 6 --through 5
```

The league is reused, never rebuilt — you keep your team and your admin rights.
The bots pick around you; when it is your turn the draft **waits** for you, and
settles the pick for you if you walk away. Leave it running in a terminal and
draft in the browser.

| Flag | Meaning |
|---|---|
| `--attach CODE` | Add bots to an existing league instead of creating one |
| `--players N` | How many bots (default 6) |
| `--through N` | Stop after round N; omit to play the full season |
| `--pick-deadline` | Pick window in seconds (default 600) |
| `--human-grace` | Seconds to wait for your pick before settling it (default 900) |
| `--seed N` | Reproducible bot behaviour |
| `--team-size N` | Override the auto-computed roster size |

A full 2025 season takes roughly 45 seconds, most of it waiting on upstream
rate limits.

### Deploy with Docker

```bash
cp .env.example .env      # SESSION_SECRET is the only required value
docker compose up -d
```

That starts Postgres, applies the schema once, and serves the app on
<http://localhost:8080>. Discord stays optional throughout.

The stack also carries the two jobs, on profiles so `up` does not run them:

```bash
# score finished races — exits when done, so a scheduler can repeat it
docker compose --profile scheduled run --rm cron

# play a league, sharing the image and the database with the web app
docker compose --profile simulate run --rm simulator --players 6 --through 5
docker compose --profile simulate run --rm simulator --attach my-league --players 6
```

Both are the same image the web service runs, so what you test is what deploys.

## CLI

| Command | Description |
|---|---|
| `python -m app.cli migrate` | Apply `db/schema.sql`. Idempotent |
| `python -m app.cli score` | Score recently finished events and announce them. Runs on a schedule |
| `python -m app.cli simulate` | Play a league, optionally against your own |

## Scoring

Every classified position scores, so a good finish matters and a bad one costs:

- **Finish points** — P1 = 20, tapering to the back of the field
- **Completion bonus** — for finishing
- **Position gain** — for places gained from the grid
- **Fastest lap** — a bonus point
- **Sprints** — at a reduced rate
- **Lapped cars still score** — they are classified finishes, not retirements

Discord announcements are output only. There is no gateway, no bot token, and no
intents.

## Architecture

```
app/
├── domain/        # Pure scoring and draft rules. Imports nothing from the project
├── provider/      # F1 data provider contract + Jolpica implementation
├── services/      # Draft and scoring orchestration
├── repo/          # Postgres queries
├── web/           # FastAPI app, routers, templates
├── sim/           # The league simulator
└── cli.py
```

Multi-tenant throughout: every league-scoped row carries `league_id`, so a
self-hosted database is just one holding a single league. No tier concept
appears in any business logic.

## Testing

```bash
pytest                      # 245 tests
ruff check app tests
ruff format --check app tests
```

Tests run against a real Postgres rather than mocks, through the real app. The
F1 data provider is stubbed so no test touches the network; the `simulate` CI
job is what exercises the real one end to end.

CI runs four gates: the test suite, lint, the schema applied twice to a live
database, and a simulated league with SQL assertions on the resulting rosters.

## Deployment

One Dockerfile serves both the web app and the cron job:

- **web** — `uvicorn app.web.app:app`, driven by `$PORT`
- **cron** — `python -m app.cli score` on a schedule, which must exit cleanly

See [.context/PLAN-RAILWAY.md](.context/PLAN-RAILWAY.md) for the service layout.

## License

MIT