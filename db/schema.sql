-- F1 Fantasy — PostgreSQL schema
--
-- Multi-tenant from day one: every league-scoped table keys on league_id, so a
-- self-hosted deployment is simply a database containing exactly one league.
-- Tier never appears in the schema.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Players ─────────────────────────────────────────────────────────────────
-- Identity is not a Discord concept. A player is a Discord account, a guest
-- (no integration at all), or something else later. `external_id` is the id
-- within that provider and is NULL for guests, so a league works with no
-- Discord integration anywhere in the stack.

CREATE TABLE IF NOT EXISTS player (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider     text NOT NULL DEFAULT 'guest'
                             CHECK (provider IN ('discord','guest')),
    external_id  text,           -- Discord snowflake; NULL for guests
    display_name text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- Guests are unique on name within a league, so uniqueness is enforced per
    -- league on `team` rather than here where a NULL external_id would collide
    -- across every guest.
    CHECK (provider <> 'discord' OR external_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS player_discord_key
    ON player (provider, external_id) WHERE provider = 'discord';

-- ── League ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS league (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code            text UNIQUE NOT NULL,
    name            text NOT NULL,
    season_year     int  NOT NULL,
    state           text NOT NULL DEFAULT 'created'
                                CHECK (state IN ('created','signup_open','draft_ready',
                                                 'drafting','active','archived')),
    team_size       int  CHECK (team_size IS NULL OR team_size > 0),
    pick_deadline   int  NOT NULL DEFAULT 600 CHECK (pick_deadline > 0),
    admin_player_id uuid REFERENCES player(id),  -- gates the admin panel
    webhook_id      text,
    webhook_token   text,          -- encrypted at rest in hosted mode
    signup_message_id text,        -- for edit-in-place signup updates
    created_at      timestamptz NOT NULL DEFAULT now(),
    archived_at     timestamptz
);

CREATE INDEX IF NOT EXISTS league_state_idx ON league (state);
CREATE INDEX IF NOT EXISTS league_season_idx ON league (season_year);

-- ── Season reference data ───────────────────────────────────────────────────

-- Season-scoped by design. Keyed on code alone this table accumulates drivers
-- across seasons and silently corrupts every driver count that reads it.
CREATE TABLE IF NOT EXISTS driver (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_year    int  NOT NULL,
    code           text NOT NULL,
    name           text NOT NULL,
    constructor    text NOT NULL,
    UNIQUE (season_year, code)
);

CREATE INDEX IF NOT EXISTS driver_season_idx ON driver (season_year);

-- ── Teams ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS team (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id     uuid NOT NULL REFERENCES league(id) ON DELETE CASCADE,
    player_id     uuid NOT NULL REFERENCES player(id),
    display_name  text NOT NULL,
    draft_order   int,
    joined_at     timestamptz NOT NULL DEFAULT now(),
    -- One team per player per league. Works for guests, whose player rows are
    -- distinct even without an external id.
    UNIQUE (league_id, player_id)
);

CREATE INDEX IF NOT EXISTS team_league_idx ON team (league_id);
CREATE INDEX IF NOT EXISTS team_player_idx ON team (player_id);

-- ── Roster ──────────────────────────────────────────────────────────────────

-- The composite primary key is the double-pick guard: two players racing for the
-- same driver resolve at the storage layer, so no application locking is needed.
CREATE TABLE IF NOT EXISTS roster (
    league_id   uuid NOT NULL REFERENCES league(id) ON DELETE CASCADE,
    team_id     uuid NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    driver_id   uuid NOT NULL REFERENCES driver(id),
    pick_number int  NOT NULL,
    picked_at   timestamptz NOT NULL DEFAULT now(),
    auto_picked boolean NOT NULL DEFAULT false,
    PRIMARY KEY (league_id, driver_id)
);

CREATE INDEX IF NOT EXISTS roster_team_idx ON roster (team_id);

-- ── Draft state ─────────────────────────────────────────────────────────────

-- pick_expires_at is the clock. There is no running task anywhere in the system;
-- expiry is a comparison evaluated lazily from any request path.
CREATE TABLE IF NOT EXISTS draft (
    league_id       uuid PRIMARY KEY REFERENCES league(id) ON DELETE CASCADE,
    status          text NOT NULL DEFAULT 'pending',
    current_pick    int  NOT NULL DEFAULT 0,
    total_picks     int  NOT NULL DEFAULT 0,
    pick_order      jsonb NOT NULL DEFAULT '[]'::jsonb,
    pick_expires_at timestamptz,
    started_at      timestamptz,
    completed_at    timestamptz
);

-- ── Race events and results ─────────────────────────────────────────────────

-- Unique on identity, never on name. Keyed on a display name it duplicates the
-- moment a calendar entry is renamed.
CREATE TABLE IF NOT EXISTS event (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id    uuid NOT NULL REFERENCES league(id) ON DELETE CASCADE,
    season_year  int  NOT NULL,
    round        int  NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('race','sprint')),
    name         text NOT NULL,
    scored_at    timestamptz,
    CONSTRAINT event_identity_key UNIQUE (league_id, season_year, round, kind)
);

CREATE INDEX IF NOT EXISTS event_league_idx ON event (league_id, round);

CREATE TABLE IF NOT EXISTS result (
    event_id    uuid NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    driver_id   uuid NOT NULL REFERENCES driver(id),
    position    int,
    grid        int,
    quali       int,
    status      text NOT NULL,
    fastest_lap boolean NOT NULL DEFAULT false,
    PRIMARY KEY (event_id, driver_id)
);

CREATE TABLE IF NOT EXISTS score (
    event_id  uuid NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    team_id   uuid NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    driver_id uuid NOT NULL REFERENCES driver(id),
    points    numeric(6,2) NOT NULL,
    breakdown jsonb,
    PRIMARY KEY (event_id, team_id, driver_id)
);

CREATE INDEX IF NOT EXISTS score_team_idx ON score (team_id);

-- ── Season archive ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS season_archive (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id        uuid NOT NULL REFERENCES league(id) ON DELETE CASCADE,
    season_year      int  NOT NULL,
    champion_team_id uuid,
    final_standings  jsonb,
    archived_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league_id, season_year)
);

-- ── Hosted tier (billing) ───────────────────────────────────────────────────
-- Present in self-hosted databases too, always empty. Keeping one schema means
-- one set of migrations and no conditional DDL.

CREATE TABLE IF NOT EXISTS subscription (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id         uuid NOT NULL REFERENCES league(id) ON DELETE CASCADE,
    stripe_customer_id text UNIQUE,
    stripe_subscription_id text UNIQUE,
    plan              text NOT NULL DEFAULT 'free',
    cadence           text CHECK (cadence IS NULL OR cadence IN ('monthly','annual')),
    status            text NOT NULL DEFAULT 'active',
    paid_until        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league_id)
);

-- ── Cron coordination ───────────────────────────────────────────────────────
-- Railway skips a scheduled run if the previous one is still going. This table
-- makes a hung run visible instead of a league that silently stops scoring.

CREATE TABLE IF NOT EXISTS job_lock (
    name       text PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz,
    completed_at timestamptz,
    error      text
);