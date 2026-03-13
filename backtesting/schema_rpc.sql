-- RPC function for distinct tickers — run in Supabase SQL Editor after schema.sql

CREATE OR REPLACE FUNCTION get_distinct_tickers(p_asset_class TEXT DEFAULT NULL)
RETURNS TABLE(ticker TEXT, asset_class TEXT) AS $$
BEGIN
    IF p_asset_class IS NOT NULL THEN
        RETURN QUERY
            SELECT DISTINCT o.ticker, o.asset_class
            FROM ohlcv_daily o
            WHERE o.asset_class = p_asset_class
            ORDER BY o.ticker;
    ELSE
        RETURN QUERY
            SELECT DISTINCT o.ticker, o.asset_class
            FROM ohlcv_daily o
            ORDER BY o.ticker;
    END IF;
END;
$$ LANGUAGE plpgsql;
