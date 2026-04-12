-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/mswexiqrcduxypevjgot/sql

-- Markets table: one row per 5-minute BTC market window
CREATE TABLE IF NOT EXISTS markets (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    condition_id TEXT NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    window_ts BIGINT NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    active BOOLEAN DEFAULT true,
    anomaly_60 INT DEFAULT 0,
    anomaly_70 INT DEFAULT 0,
    anomaly_80 INT DEFAULT 0,
    anomaly_90 INT DEFAULT 0
);

-- Price snapshots: every YES/NO price observation
CREATE TABLE IF NOT EXISTS price_snapshots (
    id SERIAL PRIMARY KEY,
    market_slug TEXT NOT NULL REFERENCES markets(slug),
    ts TIMESTAMPTZ NOT NULL,
    up_best_ask REAL NOT NULL,
    down_best_ask REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'websocket'
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market ON price_snapshots(market_slug);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON price_snapshots(ts DESC);

-- Anomaly events: each threshold crossing
CREATE TABLE IF NOT EXISTS anomaly_events (
    id SERIAL PRIMARY KEY,
    market_slug TEXT NOT NULL REFERENCES markets(slug),
    ts TIMESTAMPTZ NOT NULL,
    threshold INT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    count INT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anomaly_market ON anomaly_events(market_slug);

-- RLS: allow anon to read all tables (dashboard uses anon key)
ALTER TABLE markets ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomaly_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon read markets" ON markets FOR SELECT TO anon USING (true);
CREATE POLICY "anon read snapshots" ON price_snapshots FOR SELECT TO anon USING (true);
CREATE POLICY "anon read anomalies" ON anomaly_events FOR SELECT TO anon USING (true);

-- Enable Realtime for live dashboard updates
-- (Also enable via Dashboard > Database > Replication for each table)
