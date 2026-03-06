<div align="center">
  <img src="https://raw.githubusercontent.com/Bobnoddle/f1-fantasy-league/main/f1-fantasy.png" alt="F1 Fantasy League" width="200" />
  
  # F1 Fantasy League Discord Bot
  
  [![Python 3.12](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://www.python.org/downloads/)
  [![Discord.py 2.3](https://img.shields.io/badge/Discord.py-2.3+-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
  [![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
  [![License MIT](https://img.shields.io/badge/License-MIT-yellow?logo=open-source-initiative&logoColor=white)](LICENSE)
  [![CI](https://github.com/Bobnoddle/f1-fantasy-league/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Bobnoddle/f1-fantasy-league/actions/workflows/ci.yml)

</div>

A lightweight Discord bot for running F1 Fantasy leagues with live snake drafts and automated race scoring.

## Features

- **Snake Draft** — Turn-based driver picking with auto-scaled team sizes
- **Auto Race Results** — Fetches results from Jolpica F1 API and scores instantly
- **Full-Field Scoring** — All 20 finishing positions earn points (position gains, completion bonuses, fastest lap)
- **Live Standings** — Leaderboard, team rosters, and per-race breakdowns
- **Season Management** — Yearly reset with historical archival
- **Low-Cost Hosting** — Runs on Raspberry Pi ($0/month)

## Quick Start

### Prerequisites
- Python 3.12+
- Discord bot token ([create app](https://discord.com/developers/applications))

### Installation

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
TOKEN=your_discord_bot_token
SEASON_YEAR=2026
```

### Running

```bash
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/league setup` | Initialize a new league for this server |
| `/league reset` | Archive season and start fresh |
| `/draft open` | Open snake draft for player signup |
| `/draft start` | Begin the draft |
| `/standings` | View current leaderboard |
| `/team <player>` | View a player's roster |
| `/race fetch` | Manually fetch latest race results |

## Scoring System

**Race Points:** P1=20, P2=16, P3=13... P11-P20=2-3 pts (all positions score)
- **Completion Bonus:** +3 per race finished
- **Position Gain:** +4 per place gained
- **Fastest Lap:** +5 points
- **DNF:** 0 points (no penalty)
- **Sprints:** ½ points

See [PLAN.md](PLAN.md) for detailed scoring analysis and validation.

## Architecture

```
bot.py                 # Discord bot entry point
├── api/               # External service clients (Jolpica F1 API)
├── cogs/              # Feature modules (draft, league, results, standings)
├── db/                # Database schema & connection handling
├── utils/             # Shared utilities (scoring, embeds, checks)
└── config.py          # Environment & settings
```

**Stack:** Python 3.12 · discord.py · SQLite (aiosqlite) · Jolpica API

## Deployment

Deploy to Raspberry Pi as a systemd service:

```bash
sudo cp f1-fantasy-league.service /etc/systemd/system/
sudo systemctl enable f1-fantasy-league
sudo systemctl start f1-fantasy-league
```

## Development

See [PLAN.md](PLAN.md) for architecture, scoring system details, and implementation notes.

## License

MIT
