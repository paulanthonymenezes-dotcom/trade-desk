-- ============================================================================
-- FINANCEFLOW MIGRATION — Bond yields, economic calendar, indicators
-- Run this in the Supabase SQL Editor
-- ============================================================================

-- 1. Add 'bond' to ohlcv_daily asset_class CHECK
ALTER TABLE ohlcv_daily DROP CONSTRAINT IF EXISTS ohlcv_daily_asset_class_check;
ALTER TABLE ohlcv_daily ADD CONSTRAINT ohlcv_daily_asset_class_check
    CHECK (asset_class IN ('equity','fx','crypto','commodity','index','bond'));

-- 2. Add 'financeflow' to ohlcv_daily source CHECK
ALTER TABLE ohlcv_daily DROP CONSTRAINT IF EXISTS ohlcv_daily_source_check;
ALTER TABLE ohlcv_daily ADD CONSTRAINT ohlcv_daily_source_check
    CHECK (source IN ('marketdata','eodhd','financeflow'));

-- 3. Economic Calendar — scheduled events (CPI, NFP, GDP, rate decisions, etc.)
CREATE TABLE IF NOT EXISTS economic_calendar (
    id              BIGSERIAL PRIMARY KEY,
    country         TEXT NOT NULL,
    report_name     TEXT NOT NULL,
    report_date     DATE NOT NULL,
    datetime        TIMESTAMPTZ,
    actual          TEXT,
    previous        TEXT,
    consensus       TEXT,
    economic_impact TEXT,
    source          TEXT DEFAULT 'financeflow',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (country, report_name, report_date)
);

CREATE INDEX IF NOT EXISTS idx_econ_cal_date ON economic_calendar (report_date);
CREATE INDEX IF NOT EXISTS idx_econ_cal_country ON economic_calendar (country);
CREATE INDEX IF NOT EXISTS idx_econ_cal_impact ON economic_calendar (economic_impact);

-- 4. Economic Indicators — macro data (GDP, inflation, unemployment, etc.)
CREATE TABLE IF NOT EXISTS economic_indicators (
    id              BIGSERIAL PRIMARY KEY,
    country         TEXT NOT NULL,
    indicator_name  TEXT NOT NULL,
    last_value      DOUBLE PRECISION,
    previous_value  DOUBLE PRECISION,
    units           TEXT,
    report_date     DATE,
    source          TEXT DEFAULT 'financeflow',
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (country, indicator_name)
);

CREATE INDEX IF NOT EXISTS idx_econ_ind_country ON economic_indicators (country);

-- 5. Enable RLS on new tables
ALTER TABLE economic_calendar ENABLE ROW LEVEL SECURITY;
ALTER TABLE economic_indicators ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON economic_calendar FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service key full access" ON economic_indicators FOR ALL USING (true) WITH CHECK (true);
