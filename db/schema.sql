-- F1 Fantasy Bot — database schema
-- All statements use CREATE TABLE IF NOT EXISTS so the file is safe to
-- re-execute on every startup.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS league (
    guild_id            INTEGER PRIMARY KEY,
    team_size           INTEGER,
    draft_timeout       INTEGER DEFAULT 600,
    season_year         INTEGER NOT NULL,
    results_channel_id  INTEGER,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calendar (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    name         TEXT NOT NULL,
    race_date    TEXT NOT NULL,
    sprint_date  TEXT,
    UNIQUE(round_number)
);

CREATE TABLE IF NOT EXISTS team (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL REFERENCES league(guild_id),
    user_id         INTEGER NOT NULL,
    user_name       TEXT NOT NULL,
    draft_order     INTEGER,
    UNIQUE(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS driver (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    team_name       TEXT NOT NULL,
    active          INTEGER DEFAULT 1,
    UNIQUE(code)
);

CREATE TABLE IF NOT EXISTS roster (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    team_id         INTEGER NOT NULL REFERENCES team(id),
    driver_id       INTEGER NOT NULL REFERENCES driver(id),
    pick_number     INTEGER NOT NULL,
    UNIQUE(guild_id, driver_id)
);

CREATE TABLE IF NOT EXISTS draft_state (
    guild_id        INTEGER PRIMARY KEY REFERENCES league(guild_id),
    status          TEXT DEFAULT 'pending',
    current_pick    INTEGER DEFAULT 0,
    total_picks     INTEGER DEFAULT 0,
    pick_order_json TEXT,
    message_id      INTEGER
);

CREATE TABLE IF NOT EXISTS race (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL REFERENCES league(guild_id),
    name            TEXT NOT NULL,
    round_number    INTEGER,
    race_type       TEXT DEFAULT 'race',
    scored_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, name, race_type)
);

CREATE TABLE IF NOT EXISTS result (
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

CREATE TABLE IF NOT EXISTS score (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         INTEGER NOT NULL REFERENCES race(id),
    team_id         INTEGER NOT NULL REFERENCES team(id),
    driver_id       INTEGER NOT NULL REFERENCES driver(id),
    points          REAL NOT NULL,
    breakdown       TEXT,
    UNIQUE(race_id, team_id, driver_id)
);

CREATE TABLE IF NOT EXISTS season_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    season_year     INTEGER NOT NULL,
    champion_user   INTEGER,
    final_standings TEXT,
    archived_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, season_year)
);
