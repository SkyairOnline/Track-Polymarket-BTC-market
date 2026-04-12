-- ============================================================
-- Polymarket BTC 5m Anomaly Tracker — Complete DB Setup
-- Run in Supabase SQL Editor to reset from scratch
-- ============================================================

-- Drop everything first (clean slate)
DROP VIEW  IF EXISTS market_anomaly_summary;
DROP TABLE IF EXISTS anomaly_events;
DROP TABLE IF EXISTS trader_trades;
DROP TABLE IF EXISTS price_snapshots;
DROP TABLE IF EXISTS markets;

-- ── markets ─────────────────────────────────────────────────
CREATE TABLE markets (
    id           SERIAL PRIMARY KEY,
    slug         TEXT UNIQUE NOT NULL,
    condition_id TEXT NOT NULL,
    up_token_id  TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    window_ts    BIGINT NOT NULL,
    end_time     TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now(),
    active       BOOLEAN DEFAULT true,
    anomaly_60   INT DEFAULT 0,
    anomaly_70   INT DEFAULT 0,
    anomaly_80   INT DEFAULT 0,
    anomaly_90   INT DEFAULT 0
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

-- ── anomaly_events ───────────────────────────────────────────
CREATE TABLE anomaly_events (
    id          SERIAL PRIMARY KEY,
    market_slug TEXT NOT NULL REFERENCES markets(slug),
    ts          TIMESTAMPTZ NOT NULL,
    threshold   INT NOT NULL,   -- 60, 70, 80, or 90
    side        TEXT NOT NULL,  -- 'Up' or 'Down'
    price       REAL NOT NULL,
    count       INT NOT NULL    -- running count for this threshold in this market
);

CREATE INDEX idx_anomaly_market ON anomaly_events(market_slug);

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

-- ── market_anomaly_summary (view) ────────────────────────────
-- Derives live anomaly counts from anomaly_events.
-- Used by the dashboard instead of the markets.anomaly_* columns.
CREATE VIEW market_anomaly_summary AS
SELECT
    market_slug,
    MAX(CASE WHEN threshold = 60 THEN count ELSE 0 END) AS anomaly_60,
    MAX(CASE WHEN threshold = 70 THEN count ELSE 0 END) AS anomaly_70,
    MAX(CASE WHEN threshold = 80 THEN count ELSE 0 END) AS anomaly_80,
    MAX(CASE WHEN threshold = 90 THEN count ELSE 0 END) AS anomaly_90
FROM anomaly_events
GROUP BY market_slug;

-- ── RLS ──────────────────────────────────────────────────────
-- Python tracker uses sb_secret key (bypasses RLS automatically).
-- Dashboard uses anon key — needs SELECT grants.

ALTER TABLE markets        ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomaly_events  ENABLE ROW LEVEL SECURITY;
ALTER TABLE trader_trades   ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon read markets"         ON markets         FOR SELECT TO anon USING (true);
CREATE POLICY "anon read price_snapshots" ON price_snapshots FOR SELECT TO anon USING (true);
CREATE POLICY "anon read anomaly_events"  ON anomaly_events  FOR SELECT TO anon USING (true);
CREATE POLICY "anon read trader_trades"   ON trader_trades   FOR SELECT TO anon USING (true);

GRANT SELECT ON market_anomaly_summary TO anon;

-- ── Realtime ─────────────────────────────────────────────────
-- After running this script, also go to:
-- Supabase Dashboard → Database → Replication
-- and toggle ON for: markets, price_snapshots, anomaly_events, trader_trades
