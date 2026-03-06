# F1 Fantasy Discord Bot — Architecture Plan

> **Last updated:** 5 March 2026
> **Status:** Final — approved for implementation

---

## 0. Summary Outline

```
┌─────────────────────────────────────────────────────────┐
│  F1 Fantasy Bot — What Gets Built                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. LEAGUE SETUP        /league setup                   │
│     └─ One league per Discord server, yearly reset      │
│                                                         │
│  2. SNAKE DRAFT         /draft open (Join + Start buttons)  │
│     ├─ Randomised order, reverses each round                │
│     ├─ Auto-scaled team size: floor(22 / players)       │
│     │    e.g. 2p=10 each · 4p=5 · 5p=4 · 22p=1        │
│     ├─ 600s auto-pick timeout (10 min)                  │
│     ├─ Rich embeds + buttons for pick UX                │
│     └─ Persists through bot restarts                    │
│                                                         │
│  3. AUTO RACE RESULTS   Automatic after each GP          │
│     ├─ Jolpica F1 API (Ergast successor)                │
│     ├─ Calendar-aware hourly loop — fires after race ends │
│     └─ Auto-scores & posts; /race fetch as override      │
│                                                         │
│  4. SCORING ENGINE       All 20 positions score         │
│     ├─ Completion bonus (+3 per race finished)          │
│     ├─ Position-gain bonus (+4 per place gained)        │
│     ├─ Fastest lap bonus (+5)                           │
│     ├─ DNF = 0 pts (no extra penalty — natural floor)   │
│     └─ Sprints at ½ points                              │
│                                                         │
│  5. STANDINGS & VIEWS   /standings /team /scores        │
│     └─ Leaderboard, roster, per-race breakdowns         │
│                                                         │
│  6. SEASON RESET        /league reset (yearly)          │
│     ├─ Archives previous season to season_archive       │
│     └─ Fresh draft each year                            │
│                                                         │
│  DEPLOY: Raspberry Pi (self-hosted, $0/mo)              │
│  STACK:  Python 3.12 · discord.py · SQLite · aiosqlite  │
│  SIZE:   ~1,100 LOC · 7 tables · <50 MB RAM at runtime  │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Overview

A lightweight Discord bot that lets a server run an F1 Fantasy league with a live snake draft and a full-field scoring system designed to make every driver pick meaningful.

**Stack:** Python 3.12 · discord.py (slash commands) · SQLite (aiosqlite) · Jolpica F1 API · Raspberry Pi self-hosted.

**Cost target:** $0/month. Bot runs on a Raspberry Pi at home. Discord bots only make outbound connections to Discord's servers — no inbound ports, no port forwarding required.

---

## 2. Core Concepts

| Concept | Description |
|---------|-------------|
| **Guild = League** | One league per Discord server. Simplifies scoping. |
| **Team** | Each player owns a team of drivers. Size auto-scales: `floor(22 / player_count)`, capped at 10. Admin can override. |
| **Snake Draft** | Turn-based picking. Order reverses each round (1→5, 5→1, 1→5…). |
| **Full-Field Scoring** | All 20 finishing positions earn points. Position-gain bonus rewards drivers who move up. |
| **Auto Results** | Race data fetched from Jolpica F1 API — fully automated, no manual entry. |
| **Yearly Reset** | Season archives at year end. Fresh draft for each new F1 season. |

---

## 3. Scoring System

The goal: a midfield driver who over-performs should outscore a front-runner who under-performs.

> **Validated:** 3-config comparison run against 2024 and 2025 real season data.
> Alt2 (below) is the confirmed scoring system. Key results:
> - 2024: 0 negative drivers, Mid/Top-4 = 50%, P1/P10 ratio = 2.71x → ⚠️ MODERATE
> - 2025: 0 negative drivers, Mid/Top-4 = 63%, P1/P10 ratio = 1.72x → ✅ GOOD
> - Draft fairness (5 players): ⚠️ 6.1% max bias in 2024, ✅ 2.9% in 2025

### 3.1 Race Finish Points (all 20 positions score)

| P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 | P10 |
|----|----|----|----|----|----|----|----|----|-----|
| 20 | 16 | 13 | 11 | 9  | 7  | 6  | 5  | 4  | 3   |

| P11 | P12 | P13 | P14 | P15 | P16 | P17 | P18 | P19 | P20 |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 3   | 3   | 2   | 2   | 2   | 2   | 2   | 2   | 2   | 2   |

> Compressed top end (P1=20 not 25) tightens the leader-to-midfield gap. Raised P11–P20 floor (2–3 pts vs 1 pt) makes completing a race meaningful for any car.

### 3.2 Qualifying Points (scaled to P15)

| P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 | P10 |
|----|----|----|----|----|----|----|----|----|-----|
| 8  | 6  | 5  | 4  | 3  | 2  | 2  | 1  | 1  | 1   |

| P11 | P12 | P13 | P14 | P15 | P16 | P17 | P18 | P19 | P20 |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 1   | 1   | 1   | 1   | 1   | 0   | 0   | 0   | 0   | 0   |

> Q2 knockouts (P11–P15) earn 1pt. Q1 knockouts (P16–P20) earn 0 — getting eliminated in Q1 is the floor.

### 3.3 Bonus Points

| Event | Points | Notes |
|-------|--------|-------|
| **Race completed** (any finish, not DNF) | +3 | Rewards reliability — the floor for backmarker picks |
| **Position gained** (grid → finish) | +4 each | Grid P15 → Finish P10 = +20 |
| **Fastest Lap** | +5 | Fully captured from API |
| **DNF** | 0 | Natural penalty: already scores 0 finish + 0 gain |
| **DSQ** | −15 | |

> **Why no DNF penalty?** A DNF already scores 0 finish points and 0 position-gain — the double-punishment
> of an extra −10 was wiping out entire seasons for drivers on unreliable cars (e.g. Bottas 2024: 19 DNFs → −190 pts
> in penalties alone). Removing it turns DNF into a natural floor rather than a trap.
> **Removed:** Podium (+3), Pole (+3), DOTD (+3), and position-loss penalty (−1).

### 3.4 Sprint Races

All race-finish points at **½ value** (rounded down). No quali points for sprint. Position delta still applies at full value.

### 3.5 Why This Works

- **Completion bonus creates a backmarker floor.** Even a Sauber that DNFs 10 times earns +3 for every race they *do* finish. Picking a backmarker is a real strategy.
- **Position gain is king (+4/place).** A driver starting P18 and finishing P10 earns: 3 (finish) + 3 (completion) + 32 (8 places × 4) = **38 pts** — vs a clean P5 finish with no places gained (9 + 3 = **12 pts**). Overtaking pays.
- **No double-punishment for DNFs.** A DNF scores 0 finish + 0 gain + 0 completion bonus — already a bad result. The old −10 penalty was pushing Alpine/Sauber drivers to −89 to −125 on the season.
- **Compressed top end.** P1=20 (not 25) cuts the P1/P10 ratio from 4–14× down to 1.7–2.7×. Front-runners are still the best picks — they're just not the only picks.
- **Quali matters all the way to P15.** Q2 knockouts still earn a point — owning a bubble driver has value.
- **Fully automated.** After `/race fetch`, zero admin input required — scores publish immediately.

> **Statistical validation:** Tested on 2024 (Verstappen dominant, high DNF year) and 2025 (competitive, low DNF year).
> Alt2 achieves 0 negative drivers in both seasons. See `analysis/report_2024.md` and `analysis/report_2025.md`.

---

## 4. Snake Draft — Full Discord UX

### 4.0 Team Size Auto-Scaling

The number of drivers per team is calculated automatically at draft start:

```
drivers_per_team = min(10, floor(total_drivers / player_count))
```

With the **2026 grid of 22 drivers:**

| Players | Drivers/Team | Total Picks | Undrafted |
|---------|-------------|------------|----------|
| 2       | 10          | 20         | 2        |
| 3       | 7           | 21         | 1        |
| 4       | 5           | 20         | 2        |
| 5       | 4           | 20         | 2        |
| 6       | 3           | 18         | 4        |
| 7       | 3           | 21         | 1        |
| 11      | 2           | 22         | 0        |
| 22      | 1           | 22         | 0        |

- **Undrafted drivers** are ineligible for scoring — incentivises picking wisely across the field.
- **Admin override:** `/league setup team_size:<n>` forces a fixed size, bypassing auto-scaling.
- **Driver pool source:** Seeded automatically from the Jolpica API at `/league setup` — no manual entry. Refreshed each season.

---

### 4.1 Phase 1: Join Period

When an admin runs `/draft open`, the bot posts a **join embed** that stays pinned:

```
┌───────────────────────────────────────────────────────────┐
│  🏁  F1 FANTASY DRAFT 2026                                │
│                                                           │
│  The draft is open! Click the button below to join.       │
│  Players signed up: 0                                     │
│                                                           │
│  Settings (calculated at start):                          │
│  • Drivers/team: auto (floor(drivers ÷ players), max 10)  │
│  • 600s pick timeout (10 minutes)                         │
│  • Snake order (randomised at start)                      │
│                                                           │
│  ┌────────────┐  ┌──────────────────┐                     │
│  │  🏎️ Join   │  │  🚀 Start (Admin) │                    │
│  └────────────┘  └──────────────────┘                     │
└───────────────────────────────────────────────────────────┘
```

As players click **Join**, the embed live-updates:

```
│  Players signed up: 4 → 5 drivers/team (22 ÷ 4 = 5)  │
│  1. @Dave                                            │
│  2. @Sarah                                           │
│  3. @Mike                                            │
│  4. @Jess                                            │
```

### 4.2 Phase 2: Draft Starts — Order Reveal

Admin clicks **Start**. Bot randomises order and posts:

```
┌──────────────────────────────────────────────────────┐
│  🎲  DRAFT ORDER (Randomised)                        │
│                                                      │
│  Round 1:  Dave → Sarah → Mike → Jess                │
│  Round 2:  Jess → Mike → Sarah → Dave    ← reversed  │
│  Round 3:  Dave → Sarah → Mike → Jess                │
│  Round 4:  Jess → Mike → Sarah → Dave                │
│  Round 5:  Dave → Sarah → Mike → Jess                │
│                                                      │
│  Total picks: 20  •  5 rounds  •  4 players          │
│  Drivers/team: 5 (floor(22 ÷ 4))                    │
│                                                      │
│  First pick in 10 seconds...                         │
└──────────────────────────────────────────────────────┘
```

### 4.3 Phase 3: Pick Turns — The Core Loop

Each turn, the bot **edits the existing draft embed** (no new messages spamming the channel) and posts a ping:

```
┌──────────────────────────────────────────────────────┐
│  🏎️  ROUND 1 — PICK 1 of 20                          │
│                                                      │
│  @Dave, it's your turn!                              │
│  ⏱️ Time remaining: 9:58                             │
│                                                      │
│  ── Available Drivers ──────────────────────         │
│  🔴 Ferrari:     Hamilton · Leclerc                  │
│  🔵 Red Bull:    Verstappen · Lawson                 │
│  🟠 McLaren:     Norris · Piastri                    │
│  🟢 Aston:       Alonso · Stroll                     │
│  ⚫ Mercedes:    Russell · Antonelli                  │
│  ... (16 more)                                       │
│                                                      │
│  ┌──────────────────────────────────────┐            │
│  │ 🔽  Select a driver to draft...      │            │
│  └──────────────────────────────────────┘            │
│                                                      │
│  Or type: /draft pick <driver_name>                  │
└──────────────────────────────────────────────────────┘
```

The **dropdown menu** (Discord Select Menu component) lists all available drivers grouped by constructor. Player can either:
- Pick from the dropdown (single click)
- Use `/draft pick Verstappen` (slash command)

> **Note on channel noise:** The pick prompt edits the single draft message in place. Only the ping (`@Dave, your turn!`) is a new message per pick — keeps the channel clean.

### 4.4 Phase 4: Pick Confirmation

After a pick, bot posts a compact confirmation and immediately pings the next player:

```
┌──────────────────────────────────────────────────────┐
│  ✅  Pick #1 — @Dave selects Max Verstappen (RBR)    │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│  🏎️  ROUND 1 — PICK 2 of 20                         │
│                                                      │
│  @Sarah, it's your turn!                             │
│  ⏱️ Time remaining: 10:00                            │
│  ...                                                 │
└──────────────────────────────────────────────────────┘
```

### 4.5 Phase 5: Auto-Pick (Timeout)

If the timer runs out:

```
┌──────────────────────────────────────────────────────┐
│  ⏰  @Mike ran out of time!                           │
│  Auto-picked: Lando Norris (McLaren)                 │
└──────────────────────────────────────────────────────┘
```

Auto-pick selects a random driver from the remaining available pool.

### 4.6 Phase 6: Draft Board (Running Status)

`/draft status` shows the live board at any time:

```
┌──────────────────────────────────────────────────────┐
│  📋  DRAFT BOARD                          Round 3/5  │
│                                                      │
│        R1          R2          R3         R4    R5   │
│  Dave  Verstappen  Alonso      ← PICKING            │
│  Sarah Hamilton    Piastri     ...                   │
│  Mike  Norris      Russell     ...                   │
│  Jess  Leclerc     Antonelli   ...                   │
│                                                      │
│  ── Still Available ──                               │
│  Lawson · Stroll · Sainz · Gasly · Ocon              │
│  Tsunoda · Hülkenberg · Magnussen · Albon            │
│  Colapinto · Bearman · Doohan · Bortoleto            │
│  Hadjar                                              │
│  (14 drivers remaining)                              │
└──────────────────────────────────────────────────────┘
```

### 4.7 Phase 7: Draft Complete

```
┌──────────────────────────────────────────────────────┐
│  🏆  DRAFT COMPLETE!                                 │
│                                                      │
│  📋 Final Rosters:                                   │
│                                                      │
│  🏎️ Dave's Team                                      │
│  Verstappen · Alonso · Gasly · Tsunoda · Magnussen   │
│                                                      │
│  🏎️ Sarah's Team                                     │
│  Hamilton · Piastri · Sainz · Hülkenberg · Doohan    │
│                                                      │
│  🏎️ Mike's Team                                      │
│  Norris · Russell · Stroll · Albon · Bearman         │
│                                                      │
│  🏎️ Jess's Team                                      │
│  Leclerc · Antonelli · Lawson · Ocon · Colapinto     │
│                                                      │
│  First race scoring starts at the next GP. 🏁        │
└──────────────────────────────────────────────────────┘
```

---

## 5. F1 API Integration (Jolpica)

### 5.1 Why Jolpica

The Ergast API shut down end-of-2024. **Jolpica** (`api.jolpi.ca/ergast/f1/`) is the community-maintained successor — same schema, same endpoints, actively updated.

### 5.2 Data Fetched

| Endpoint | Data | Used For |
|----------|------|----------|
| `/{year}/{round}/qualifying` | Quali positions P1–P20 | Quali scoring |
| `/{year}/{round}/results` | Finish positions, grid, status, fastest lap | Race scoring |
| `/{year}/{round}/sprint` | Sprint finish & grid | Sprint scoring |
| `/{year}/drivers` | Driver list + constructors | Season driver seed |
| `/{year}/races` | Race calendar | Auto-fetch loop (date checks) + `/race fetch` autocomplete |

### 5.3 Flow

```
Bot startup:
  └─ Fetches /{year}/races and /{year}/drivers → cached in SQLite
     (populates `calendar` table with round dates/times)

Hourly background task (discord.ext.tasks):
  ├─ For each guild with an active league:
  ├─ Query `calendar` for any event in the past 36h not yet in `race` table
  ├─ If found → call Jolpica API (quali + race + sprint if applicable)
  ├─ If results available → score, write to `result` + `score`, post embed
  └─ If results not yet published (API returns empty) → skip, retry next hour

Admin override: /race fetch → autocomplete shows unscored rounds
  └─ Same scoring path as auto-fetch; used if auto-fetch misses or needs re-run
```

> The loop only acts during the ~36h window after a scheduled race. Zero work in off-weeks.

### 5.4 Resource Efficiency

- **Calendar-aware loop.** `@tasks.loop(hours=1)` fires every hour but only calls Jolpica when a race event has occurred in the last 36h and isn't yet scored — zero API calls in off-weeks (~46 weeks/year).
- **Startup cache.** Race calendar and driver list fetched once at startup, written to SQLite `calendar` table. Powers `/race fetch` autocomplete and the loop's date checks.
- **One-shot HTTP per event.** Each score run = 2–3 GET requests (quali, race, optionally sprint). Committed to SQLite immediately, never re-fetched.
- **aiohttp session** reused across requests, closed on bot shutdown.
- **Fully automated scoring.** No DOTD, no manual flags — scores post without any admin action.

---

## 6. Bot Commands (Complete)

### League Management (Admin)
| Command | Description |
|---------|-------------|
| `/league setup [team_size] [timeout] [channel]` | Initialise league — channel defaults to current channel if omitted |
| `/league reset` | Archive season & reset (yearly) |

### Draft
| Command | Description |
|---------|-------------|
| `/draft open` | Open draft — posts embed with Join button and admin Start button |
| `/draft pick <driver>` | Fallback slash command for picking (dropdown in embed is primary) |
| `/draft status` | Show full draft board |

### Race & Results
| Command | Description |
|---------|-------------|
| `/race fetch` | Admin override — manually trigger scoring for a specific round (autocomplete) |

### Standings & Info
| Command | Description |
|---------|-------------|
| `/standings` | Season leaderboard |
| `/team [@player]` | View roster + total points |
| `/scores <race_name>` | Detailed per-driver breakdown |
| `/rules` | Show scoring rules embed |

---

## 7. Data Model (SQLite)

```sql
-- League config per guild
CREATE TABLE league (
    guild_id            INTEGER PRIMARY KEY,
    team_size           INTEGER,                 -- NULL = auto-scaled
    draft_timeout       INTEGER DEFAULT 600,    -- seconds (10 min)
    season_year         INTEGER NOT NULL,        -- e.g. 2026
    results_channel_id  INTEGER,                 -- channel where auto-scored results post
    created_at          TEXT DEFAULT (datetime('now'))
);

-- Race calendar (populated from Jolpica at startup, used by auto-fetch loop)
CREATE TABLE calendar (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    name         TEXT NOT NULL,           -- "Australian Grand Prix"
    race_date    TEXT NOT NULL,           -- ISO 8601 UTC datetime of race start
    sprint_date  TEXT,                    -- NULL if no sprint weekend
    UNIQUE(round_number)
);

-- Players in the league
CREATE TABLE team (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL REFERENCES league(guild_id),
    user_id         INTEGER NOT NULL,
    user_name       TEXT NOT NULL,
    draft_order     INTEGER,
    UNIQUE(guild_id, user_id)
);

-- F1 drivers (refreshed from API each season)
CREATE TABLE driver (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,           -- e.g. "VER", "HAM"
    name            TEXT NOT NULL,           -- "Max Verstappen"
    team_name       TEXT NOT NULL,           -- "Red Bull"
    active          INTEGER DEFAULT 1,
    UNIQUE(code)
);

-- Draft picks (also serves as roster)
CREATE TABLE roster (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    team_id         INTEGER NOT NULL REFERENCES team(id),
    driver_id       INTEGER NOT NULL REFERENCES driver(id),
    pick_number     INTEGER NOT NULL,        -- overall pick # (round derivable from this)
    UNIQUE(guild_id, driver_id)              -- no duplicate drivers per league
);

-- Draft session state (survives restarts)
CREATE TABLE draft_state (
    guild_id        INTEGER PRIMARY KEY REFERENCES league(guild_id),
    status          TEXT DEFAULT 'pending',  -- pending | open | active | complete
    current_pick    INTEGER DEFAULT 0,
    total_picks     INTEGER DEFAULT 0,
    pick_order_json TEXT,                    -- JSON array of team_ids in snake order
    message_id      INTEGER                  -- ID of the live draft embed to edit
);

-- Race events
CREATE TABLE race (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL REFERENCES league(guild_id),
    name            TEXT NOT NULL,
    round_number    INTEGER,                 -- F1 calendar round
    race_type       TEXT DEFAULT 'race',     -- race | sprint | qualifying
    scored_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, name, race_type)
);

-- Individual driver results per race
CREATE TABLE result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         INTEGER NOT NULL REFERENCES race(id),
    driver_id       INTEGER NOT NULL REFERENCES driver(id),
    grid_position   INTEGER,
    finish_position INTEGER,
    dnf             INTEGER DEFAULT 0,
    dsq             INTEGER DEFAULT 0,
    fastest_lap     INTEGER DEFAULT 0,
    quali_position  INTEGER,
    UNIQUE(race_id, driver_id)
);

-- Cached scores (computed on result entry, read for standings)
CREATE TABLE score (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         INTEGER NOT NULL REFERENCES race(id),
    team_id         INTEGER NOT NULL REFERENCES team(id),
    driver_id       INTEGER NOT NULL REFERENCES driver(id),
    points          REAL NOT NULL,
    breakdown       TEXT,                    -- JSON: {"finish": 13, "delta": 16, "fastest_lap": 5, ...}
    UNIQUE(race_id, driver_id)
);

-- Season archive (for yearly reset)
CREATE TABLE season_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    season_year     INTEGER NOT NULL,
    champion_user   INTEGER,                 -- winner's Discord user ID
    final_standings TEXT,                    -- JSON snapshot of final leaderboard
    archived_at     TEXT DEFAULT (datetime('now'))
);
```

---

## 8. Project Structure

```
f1-bot/
├── bot.py                  # Entry point — bot init, cog loading, startup cache
├── config.py               # Env vars, scoring tables, constants
├── requirements.txt        # discord.py, aiosqlite, aiohttp, python-dotenv
├── f1bot.service           # systemd unit file (auto-start + restart on Pi)
├── .env.example            # DISCORD_TOKEN=
│
├── db/
│   ├── connection.py       # Async SQLite connection (single shared)
│   └── schema.sql          # All CREATE TABLE statements
│
├── cogs/
│   ├── league.py           # /league setup, reset
│   ├── draft.py            # /draft open, start, pick, status + snake logic
│   ├── results.py          # Auto-fetch loop + /race fetch override — API → score → post
│   └── standings.py        # /standings, /team, /scores, /rules
│
├── api/
│   └── jolpica.py          # Jolpica F1 API client (aiohttp)
│
└── utils/
    ├── scoring.py          # Pure-function scoring calculator (no I/O)
    ├── embeds.py           # Rich Discord embed builders
    └── checks.py           # Permission/state decorators
```

---

## 9. Resource Efficiency (Raspberry Pi)

| Concern | Solution |
|---------|----------|
| **RAM** | Bot idles at ~30–40 MB. Any Pi with 512 MB+ RAM is fine (Pi 3B, 4, Zero 2 W). |
| **CPU** | Event-driven asyncio. ~0% CPU when idle. Spikes briefly on slash commands. |
| **Network** | Outbound-only WebSocket to Discord. Hourly loop calls Jolpica only in the 36h post-race window (~48 calls/year total). No inbound ports needed. |
| **Disk** | SQLite DB < 1 MB for a full season. Store on a USB drive (not SD card) to avoid write-wear. |
| **Uptime** | `systemd` service with `Restart=always`. Bot auto-starts on boot, auto-restarts on crash. |
| **Updates** | `git pull && systemctl restart f1bot` — done. |

### Cost Estimate

| Item | Cost |
|------|------|
| **Raspberry Pi** (one-time) | ~$15–35 (Pi Zero 2 W or Pi 3B) |
| **Electricity** | ~$1–2/year (Pi Zero 2 W at 0.5W idle) |
| **Ongoing monthly cost** | **$0** |

### systemd Service (`f1bot.service`)

```ini
[Unit]
Description=F1 Fantasy Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/f1-bot
EnvironmentFile=/home/pi/f1-bot/.env
ExecStart=/home/pi/f1-bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Install:**
```bash
sudo cp f1bot.service /etc/systemd/system/
sudo systemctl enable f1bot
sudo systemctl start f1bot
```

---

## 10. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Python + discord.py** | Fastest path to working bot. ~1,220 LOC. Mature slash-command + component (buttons/dropdowns) support. |
| **SQLite (aiosqlite)** | Zero infra cost. Single file. <1 MB for a full season. Perfect for <50 users. |
| **No ORM** | 7 simple tables. Raw SQL keeps it tiny and debuggable. |
| **Guild = League** | Eliminates multi-tenancy complexity. One server, one league. |
| **Jolpica API** | Free, no auth required, community-maintained Ergast successor. Eliminates manual data entry. |
| **Startup cache for race calendar** | Powers `/race fetch` autocomplete with zero latency. Fetched once at boot, no round-trip on command. |
| **Auto-fetch loop** (`discord.ext.tasks`) | Calendar-aware `@tasks.loop(hours=1)` — checks SQLite `calendar` for unscored past events, fetches and posts automatically. Admin `/race fetch` is an override, not the primary path. |
| **Scoring cached in `score` table** | Compute once on fetch, read fast for standings. JSON breakdown for display. |
| **Draft state in DB** | Survives Pi reboots and crashes without losing draft progress. |
| **Pick prompt edits in place** | Single draft embed updated per pick — no channel spam. |
| **Dropdown + slash command** for picks | Dropdown is faster (one click), slash command is fallback. Both work. |
| **Season archive table** | Keeps historical champions without cluttering active tables. |
| **No trading** | Roster is set at draft. Auto-scaling makes every pick meaningful — swapping mid-season adds complexity without changing outcomes meaningfully. |
| **systemd service** | Auto-starts on boot, auto-restarts on crash. Zero-maintenance uptime. |

---

## 11. Implementation Order

| Phase | What | Est. LOC | Depends On |
|-------|------|----------|------------|
| **1** | Scaffold: bot.py, config, DB schema, systemd service, .env | ~150 | — |
| **2** | Jolpica API client + startup cache (drivers, race calendar → `calendar` table) | ~130 | Phase 1 |
| **3** | League setup + snake draft (embeds, buttons, dropdown, timeout) | ~400 | Phase 1 |
| **4** | Scoring engine (pure functions, `utils/scoring.py`) | ~100 | — |
| **5** | Auto-fetch loop + `/race fetch` override → autocomplete → API → score → embed | ~220 | Phase 2, 4 |
| **6** | Standings, team view, leaderboard, rules embeds | ~150 | Phase 4 |
| **7** | Season reset + archive | ~70 | Phase 6 |
| **8** | Polish: error handling, validation, edge cases | ~70 | All |

**Total estimate: ~1,220 LOC**

---

`[STATUS: READY FOR IMPLEMENTATION]`
