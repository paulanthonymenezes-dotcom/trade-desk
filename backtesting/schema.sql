-- ============================================================================
-- BACKTESTING ENGINE — Supabase Schema
-- Run this in the Supabase SQL Editor
-- ============================================================================

-- 1. OHLCV Daily
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    date            DATE NOT NULL,
    open            DOUBLE PRECISION,
    high            DOUBLE PRECISION,
    low             DOUBLE PRECISION,
    close           DOUBLE PRECISION NOT NULL,
    volume          BIGINT,
    asset_class     TEXT NOT NULL CHECK (asset_class IN ('equity','fx','crypto','commodity','index')),
    source          TEXT NOT NULL CHECK (source IN ('marketdata','eodhd')),
    adjusted_flag   BOOLEAN DEFAULT TRUE,
    UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv_daily (ticker, date DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_asset_class ON ohlcv_daily (asset_class);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv_daily (date);

-- 2. Fundamentals
CREATE TABLE IF NOT EXISTS fundamentals (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    date            DATE NOT NULL,
    pe_ratio        DOUBLE PRECISION,
    market_cap      DOUBLE PRECISION,
    debt_to_equity  DOUBLE PRECISION,
    revenue_growth  DOUBLE PRECISION,
    eps             DOUBLE PRECISION,
    sector          TEXT,
    industry        TEXT,
    UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker ON fundamentals (ticker);
CREATE INDEX IF NOT EXISTS idx_fundamentals_sector ON fundamentals (sector);

-- 3. Events
CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN (
        'rate_decision','oil_shock','geopolitical','market_structure','macro_surprise'
    )),
    magnitude       DOUBLE PRECISION,
    geography       TEXT,
    direction       TEXT,
    description     TEXT NOT NULL,
    source          TEXT,
    tags            TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events (date);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);

-- 4. Earnings Dates (for exclusion logic)
CREATE TABLE IF NOT EXISTS earnings_dates (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    date            DATE NOT NULL,
    UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_ticker_date ON earnings_dates (ticker, date);

-- 5. Backtest Results
CREATE TABLE IF NOT EXISTS backtest_results (
    id              BIGSERIAL PRIMARY KEY,
    pattern_id      TEXT NOT NULL,
    conditions      JSONB NOT NULL,
    ticker          TEXT NOT NULL,
    asset_class     TEXT NOT NULL,
    sample_size     INTEGER NOT NULL,
    win_rate        DOUBLE PRECISION,
    avg_return_d1   DOUBLE PRECISION,
    avg_return_d2   DOUBLE PRECISION,
    avg_return_d5   DOUBLE PRECISION,
    avg_return_d10  DOUBLE PRECISION,
    median_return   DOUBLE PRECISION,
    sharpe          DOUBLE PRECISION,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_pattern ON backtest_results (pattern_id);
CREATE INDEX IF NOT EXISTS idx_backtest_ticker ON backtest_results (ticker);

-- 6. Helper: Trading calendar (US market holidays excluded)
-- Materialized view for fast 200-day MA, streak calcs, etc.
CREATE OR REPLACE VIEW ohlcv_with_returns AS
SELECT
    *,
    (close / NULLIF(LAG(close) OVER (PARTITION BY ticker ORDER BY date), 0) - 1) * 100 AS daily_return_pct,
    LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS prev_close,
    close - LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS daily_change,
    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date) AS trading_day_num
FROM ohlcv_daily;

-- 7. Enable RLS but allow service key full access
ALTER TABLE ohlcv_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE fundamentals ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE earnings_dates ENABLE ROW LEVEL SECURITY;
ALTER TABLE backtest_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON ohlcv_daily FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service key full access" ON fundamentals FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service key full access" ON events FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service key full access" ON earnings_dates FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service key full access" ON backtest_results FOR ALL USING (true) WITH CHECK (true);
