from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from supabase import create_client
from backtesting.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, DB_BATCH_SIZE

# Cached universe files live next to this module
_DATA_DIR = Path(__file__).resolve().parent / "data"


def get_client():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


_CONFLICT_KEYS = {
    "ohlcv_daily": "ticker,date",
    "earnings_dates": "ticker,date",
    "economic_calendar": "country,report_name,report_date",
    "economic_indicators": "country,indicator_name",
    "macro_timeseries": "country_code,indicator_code,year",
}


def upsert_batch(table: str, rows: list[dict], batch_size: int = DB_BATCH_SIZE):
    """Upsert rows in batches. Returns total rows upserted."""
    client = get_client()
    conflict_key = _CONFLICT_KEYS.get(table, "ticker,date")
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        client.table(table).upsert(chunk, on_conflict=conflict_key).execute()
        total += len(chunk)
    return total


def insert_batch(table: str, rows: list[dict], batch_size: int = DB_BATCH_SIZE):
    """Insert rows in batches (no conflict resolution)."""
    client = get_client()
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        client.table(table).insert(chunk).execute()
        total += len(chunk)
    return total


def fetch_ohlcv(ticker: str, start_date: str = None, end_date: str = None) -> list[dict]:
    """Fetch ALL OHLCV data for a ticker, ordered by date.

    Paginates through Supabase's default 1000-row limit to get full history.
    """
    client = get_client()
    all_rows = []
    page_size = 1000
    offset = 0

    while True:
        q = client.table("ohlcv_daily").select("*").eq("ticker", ticker).order("date")
        if start_date:
            q = q.gte("date", start_date)
        if end_date:
            q = q.lte("date", end_date)
        q = q.range(offset, offset + page_size - 1)
        result = q.execute()
        rows = result.data
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return all_rows


def fetch_ohlcv_multi(tickers: list[str], start_date: str = None, end_date: str = None) -> list[dict]:
    """Fetch ALL OHLCV for multiple tickers with pagination."""
    client = get_client()
    all_rows = []
    page_size = 1000
    offset = 0

    while True:
        q = client.table("ohlcv_daily").select("*").in_("ticker", tickers).order("date")
        if start_date:
            q = q.gte("date", start_date)
        if end_date:
            q = q.lte("date", end_date)
        q = q.range(offset, offset + page_size - 1)
        result = q.execute()
        rows = result.data
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return all_rows


def fetch_ohlcv_batch(
    tickers: list[str],
    start_date: str = None,
    end_date: str = None,
    columns: str = "ticker,date,close",
    max_workers: int = 15,
) -> dict[str, list[dict]]:
    """Bulk-fetch OHLCV for many tickers using parallel individual queries.

    Returns {ticker: [rows sorted by date]}.
    Uses ThreadPoolExecutor to fetch each ticker concurrently.
    Individual .eq() queries are much faster than .in_() with many tickers.
    """

    def _fetch_one(ticker: str) -> tuple[str, list[dict]]:
        client = get_client()
        rows = []
        page_size = 1000
        offset = 0
        while True:
            q = (
                client.table("ohlcv_daily")
                .select(columns)
                .eq("ticker", ticker)
                .order("date")
            )
            if start_date:
                q = q.gte("date", start_date)
            if end_date:
                q = q.lte("date", end_date)
            q = q.range(offset, offset + page_size - 1)
            result = q.execute()
            rows.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size
        return ticker, rows

    if not tickers:
        return {}

    by_ticker: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tickers))) as executor:
        futures = [executor.submit(_fetch_one, t) for t in tickers]
        for future in as_completed(futures):
            try:
                ticker, rows = future.result()
                if rows:
                    by_ticker[ticker] = rows
            except Exception as e:
                print(f"  Warning: fetch error: {e}")

    return by_ticker


def fetch_earnings_dates_bulk(tickers: list[str]) -> dict[str, list[str]]:
    """Bulk-fetch earnings dates for many tickers.

    Returns {ticker: [date strings sorted]}.
    """
    if not tickers:
        return {}

    client = get_client()
    all_rows: list[dict] = []
    batch_size = 100
    page_size = 1000

    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        offset = 0
        while True:
            result = (
                client.table("earnings_dates")
                .select("ticker,date")
                .in_("ticker", chunk)
                .order("date")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            all_rows.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

    by_ticker: dict[str, list[str]] = {}
    for row in all_rows:
        by_ticker.setdefault(row["ticker"], []).append(row["date"])
    return by_ticker


def fetch_events(event_type: str = None, start_date: str = None, end_date: str = None) -> list[dict]:
    """Fetch events, optionally filtered."""
    client = get_client()
    q = client.table("events").select("*").order("date")
    if event_type:
        q = q.eq("event_type", event_type)
    if start_date:
        q = q.gte("date", start_date)
    if end_date:
        q = q.lte("date", end_date)
    return q.execute().data


def fetch_earnings_dates(ticker: str) -> list[str]:
    """Fetch earnings dates for a ticker."""
    client = get_client()
    result = client.table("earnings_dates").select("date").eq("ticker", ticker).order("date").execute()
    return [r["date"] for r in result.data]


def fetch_economic_calendar(
    country: str = None,
    start_date: str = None,
    end_date: str = None,
    impact: str = None,
) -> list[dict]:
    """Fetch economic calendar events, optionally filtered."""
    client = get_client()
    q = client.table("economic_calendar").select("*").order("report_date")
    if country:
        q = q.eq("country", country)
    if start_date:
        q = q.gte("report_date", start_date)
    if end_date:
        q = q.lte("report_date", end_date)
    if impact:
        q = q.eq("economic_impact", impact)
    return q.execute().data


def fetch_economic_indicators(country: str = None) -> list[dict]:
    """Fetch economic indicators, optionally filtered by country."""
    client = get_client()
    q = client.table("economic_indicators").select("*").order("country")
    if country:
        q = q.eq("country", country)
    return q.execute().data


# ── Ticker universe cache ──────────────────────────────────────────────────

def save_equity_universe(tickers: list[str]) -> None:
    """Persist the equity ticker list to a local JSON cache.

    Called by seed scripts after determining the universe.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATA_DIR / "equity_universe.json"
    with open(path, "w") as f:
        json.dump(sorted(set(tickers)), f)
    print(f"  Saved {len(tickers)} tickers to {path}")


def get_equity_universe() -> list[str]:
    """Get the cached equity ticker universe.

    Reads from local JSON cache (populated during seeding).
    Falls back to a DB query filtered by asset_class='equity' if
    cache is missing — but uses a much smarter approach than
    paginating through all 18M+ OHLCV rows.
    """
    # 1. Try cached file first (fast — <1ms)
    cache_path = _DATA_DIR / "equity_universe.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    # 2. Fallback: sample distinct tickers from DB by letter prefix
    #    This avoids full-table-scan by querying 26 small filtered ranges
    print("  Universe cache not found — querying DB for equity tickers...")
    try:
        client = get_client()
        all_tickers = set()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            next_letter = chr(ord(letter) + 1) if letter < "Z" else "ZZ"
            offset = 0
            page_size = 1000
            while True:
                result = (
                    client.table("ohlcv_daily")
                    .select("ticker")
                    .eq("asset_class", "equity")
                    .gte("ticker", letter)
                    .lt("ticker", next_letter)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                for row in result.data:
                    all_tickers.add(row["ticker"])
                if len(result.data) < page_size:
                    break
                offset += page_size
                # Safety: stop after 50k rows per letter
                if offset > 50_000:
                    break

        tickers = sorted(all_tickers)
        if tickers:
            save_equity_universe(tickers)
        return tickers
    except Exception as e:
        print(f"  Warning: could not fetch universe from DB: {e}")
        return []
