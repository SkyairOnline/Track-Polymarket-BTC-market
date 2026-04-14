-- ============================================================
-- Polymarket BTC 5m Tracker — Complete DB Setup
-- Run in Supabase SQL Editor to reset from scratch
-- ============================================================

-- Drop everything first (clean slate)
DROP TABLE IF EXISTS trader_trades;
DROP TABLE IF EXISTS price_snapshots;
DROP TABLE IF EXISTS markets;

-- ── markets ─────────────────────────────────────────────────
CREATE TABLE markets (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    condition_id  TEXT NOT NULL,
    up_token_id   TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    window_ts     BIGINT NOT NULL,
    end_time      TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now(),
    active        BOOLEAN DEFAULT true
);

-- ── price_snapshots ─────────────────────────────────────────
CREATE TABLE price_snapshots (
    id             SERIAL PRIMARY KEY,
    market_slug    TEXT NOT NULL REFERENCES markets(slug),
    ts             TIMESTAMPTZ NOT NULL,
    -- UP token (YES outcome)
    up_best_ask    REAL,
    up_worst_ask   REAL,
    up_best_bid    REAL,
    up_worst_bid   REAL,
    -- DOWN token (NO outcome)
    down_best_ask  REAL,
    down_worst_ask REAL,
    down_best_bid  REAL,
    down_worst_bid REAL,
    source         TEXT NOT NULL DEFAULT 'rest_book'
);

CREATE INDEX idx_snapshots_market ON price_snapshots(market_slug);
CREATE INDEX idx_snapshots_ts     ON price_snapshots(ts DESC);

-- ── trader_trades ────────────────────────────────────────────
CREATE TABLE trader_trades (
    id               SERIAL PRIMARY KEY,
    market_slug      TEXT NOT NULL REFERENCES markets(slug),
    ts               TIMESTAMPTZ NOT NULL,
    side             TEXT NOT NULL,   -- 'BUY' or 'SELL'
    outcome          TEXT NOT NULL,   -- 'Up' or 'Down'
    price            REAL NOT NULL,
    size             REAL NOT NULL,   -- USDC size
    transaction_hash TEXT UNIQUE NOT NULL,
    role             TEXT NOT NULL DEFAULT 'activity'
);

CREATE INDEX idx_trader_trades_market ON trader_trades(market_slug);
CREATE INDEX idx_trader_trades_ts     ON trader_trades(ts DESC);

-- ── RLS ──────────────────────────────────────────────────────
-- Python tracker uses sb_secret key (bypasses RLS automatically).
-- Dashboard uses anon key — needs SELECT grants.

ALTER TABLE markets         ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE trader_trades   ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon read markets"         ON markets         FOR SELECT TO anon USING (true);
CREATE POLICY "anon read price_snapshots" ON price_snapshots FOR SELECT TO anon USING (true);
CREATE POLICY "anon read trader_trades"   ON trader_trades   FOR SELECT TO anon USING (true);

-- ── Realtime ─────────────────────────────────────────────────
-- After running this script, also go to:
-- Supabase Dashboard → Database → Replication
-- and toggle ON for: markets, price_snapshots, trader_trades
