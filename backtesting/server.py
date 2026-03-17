"""FastAPI server exposing the pattern scanner as an API.

Run with: uvicorn backtesting.server:app --reload --port 8787
"""
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backtesting.scanner.pattern_scanner import scan_pattern, scan_universe
from backtesting.scanner.conditions import CONDITION_REGISTRY
from backtesting.db import get_client, fetch_ohlcv, fetch_economic_calendar, fetch_economic_indicators, get_equity_universe
from backtesting.ai_query import ask_scanner, explain_results
from backtesting.event_detector import detect_events, CENTRAL_BANK_CALENDAR
from backtesting.market_data import get_market_overview
from backtesting.wrappers.financeflow import get_financial_calendar

app = FastAPI(title="Edge Scanner API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ───────────────────────────────────────────────────────────────────

class ConditionInput(BaseModel):
    type: str
    params: dict = {}
    ticker: Optional[str] = None  # For cross-asset secondary conditions


class ScanRequest(BaseModel):
    primary_ticker: str
    primary_conditions: List[ConditionInput]
    target_ticker: Optional[str] = None
    secondary_conditions: Optional[List[ConditionInput]] = None
    fundamental_filters: Optional[dict] = None
    exclude_earnings: bool = True
    earnings_window: int = 3
    horizons: List[int] = [1, 2, 5, 10]


class UniverseScanRequest(BaseModel):
    primary_ticker: str
    primary_conditions: List[ConditionInput]
    target_tickers: List[str]
    secondary_conditions: Optional[List[ConditionInput]] = None
    exclude_earnings: bool = True
    horizons: List[int] = [1, 2, 5, 10]


class AskRequest(BaseModel):
    question: str
    explain: bool = True  # Whether to generate AI explanation of results


class EventInput(BaseModel):
    date: str
    event_type: str
    magnitude: Optional[float] = None
    geography: Optional[str] = None
    direction: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[List[str]] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/conditions")
def list_conditions():
    """List all available condition types and their parameters."""
    return {
        name: {
            "description": info["description"],
            "params": {k: v for k, v in info["params"].items() if k != "vix_df"},
        }
        for name, info in CONDITION_REGISTRY.items()
    }


@app.get("/api/tickers")
def list_tickers(asset_class: str = None):
    """List available tickers, optionally filtered by asset class.

    For equities, uses the fast cached universe file.
    For other asset classes, queries the DB (small tables).
    """
    if asset_class == "equity" or asset_class is None:
        # Fast path: cached equity universe
        equity_tickers = get_equity_universe()
        equity_list = [{"ticker": t, "asset_class": "equity"} for t in equity_tickers]

        if asset_class == "equity":
            return equity_list

    # For non-equity or "all", query smaller asset classes from DB
    non_equity = []
    if asset_class is None or asset_class != "equity":
        client = get_client()
        q = client.table("ohlcv_daily").select("ticker, asset_class")
        if asset_class:
            q = q.eq("asset_class", asset_class)
        else:
            q = q.neq("asset_class", "equity")  # Equity handled above
        raw = q.limit(5000).execute()
        seen = set()
        for r in raw.data:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                non_equity.append({"ticker": r["ticker"], "asset_class": r["asset_class"]})

    if asset_class is None:
        return equity_list + non_equity
    return non_equity


@app.get("/api/tickers/search")
def search_tickers(q: str):
    """Search tickers by prefix.

    Uses the cached equity universe for fast prefix search,
    falls back to DB for non-equity matches.
    """
    q_upper = q.upper().strip()
    if not q_upper:
        return []

    # Search cached equity universe first (instant)
    equity_tickers = get_equity_universe()
    matches = [
        {"ticker": t, "asset_class": "equity"}
        for t in equity_tickers
        if t.startswith(q_upper)
    ][:30]

    # Also search non-equity tickers in DB (small table)
    try:
        client = get_client()
        result = (
            client.table("ohlcv_daily")
            .select("ticker, asset_class")
            .neq("asset_class", "equity")
            .ilike("ticker", f"{q}%")
            .limit(20)
            .execute()
        )
        seen = {m["ticker"] for m in matches}
        for r in result.data:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                matches.append({"ticker": r["ticker"], "asset_class": r["asset_class"]})
    except Exception:
        pass  # Non-equity search is best-effort

    return matches[:50]


@app.post("/api/scan")
def run_scan(req: ScanRequest):
    """Run a pattern scan with the given conditions."""
    result = scan_pattern(
        primary_ticker=req.primary_ticker,
        primary_conditions=[c.model_dump() for c in req.primary_conditions],
        target_ticker=req.target_ticker,
        secondary_conditions=[c.model_dump() for c in req.secondary_conditions] if req.secondary_conditions else None,
        fundamental_filters=req.fundamental_filters,
        exclude_earnings=req.exclude_earnings,
        earnings_window=req.earnings_window,
        horizons=req.horizons,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/scan/universe")
def run_universe_scan(req: UniverseScanRequest):
    """Run a pattern scan across multiple target tickers."""
    results = scan_universe(
        primary_ticker=req.primary_ticker,
        primary_conditions=[c.model_dump() for c in req.primary_conditions],
        target_tickers=req.target_tickers,
        secondary_conditions=[c.model_dump() for c in req.secondary_conditions] if req.secondary_conditions else None,
        exclude_earnings=req.exclude_earnings,
        horizons=req.horizons,
    )
    return results


@app.get("/api/events")
def list_events(event_type: str = None, search: str = None):
    """List events, optionally filtered by type or search term."""
    client = get_client()
    q = client.table("events").select("*").order("date", desc=True)
    if event_type:
        q = q.eq("event_type", event_type)
    if search:
        q = q.ilike("description", f"%{search}%")
    return q.limit(500).execute().data


@app.post("/api/events")
def create_event(event: EventInput):
    """Create a new macro event."""
    client = get_client()
    row = event.model_dump(exclude_none=True)
    result = client.table("events").insert(row).execute()
    if result.data:
        return result.data[0]
    raise HTTPException(status_code=400, detail="Failed to create event")


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
    """Delete an event by ID."""
    client = get_client()
    result = client.table("events").delete().eq("id", event_id).execute()
    return {"deleted": True, "id": event_id}


@app.get("/api/backtest_results")
def list_backtest_results(ticker: str = None, limit: int = 50):
    """List saved backtest results."""
    client = get_client()
    q = client.table("backtest_results").select("*").order("created_at", desc=True)
    if ticker:
        q = q.eq("ticker", ticker)
    return q.limit(limit).execute().data


@app.get("/api/ohlcv/{ticker}")
def get_ohlcv(ticker: str, start: str = None, end: str = None):
    """Fetch OHLCV data for a ticker."""
    data = fetch_ohlcv(ticker, start, end)
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {ticker}")
    return data


# ── Auto Event Detection ──────────────────────────────────────────────────

@app.post("/api/events/detect")
async def detect_new_events(lookback: int = 7, dry_run: bool = False):
    """Auto-detect macro events from recent price action.

    Monitors oil, VIX, SPY, QQQ, gold, bonds, etc. for:
    - Big daily moves (e.g., SPY -3%, oil +6%)
    - Consecutive down/up streaks (5+ days)
    - Price level crossings (oil above $100, etc.)
    - Big weekly moves

    Set dry_run=true to preview without inserting.
    """
    events = await detect_events(lookback_days=lookback, dry_run=dry_run)
    return {
        "detected": len(events),
        "dry_run": dry_run,
        "events": [
            {"date": e["date"], "event_type": e["event_type"], "description": e["description"], "magnitude": e.get("magnitude")}
            for e in events
        ],
    }


@app.get("/api/central-banks")
def get_central_bank_calendar():
    """Get the central bank meeting calendar with current rates and upcoming dates."""
    from datetime import date as dt_date

    today = dt_date.today()
    result = []

    for cb_key, info in CENTRAL_BANK_CALENDAR.items():
        all_dates = sorted(info["dates"])
        # Find next upcoming meeting
        upcoming = [d for d in all_dates if d >= today.isoformat()]
        past = [d for d in all_dates if d < today.isoformat()]
        next_meeting = upcoming[0] if upcoming else None
        last_meeting = past[-1] if past else None

        # Days until next meeting
        days_until = None
        if next_meeting:
            days_until = (dt_date.fromisoformat(next_meeting) - today).days

        result.append({
            "code": cb_key,
            "bank": info["bank"],
            "geography": info["geography"],
            "currency": info.get("currency", ""),
            "current_rate": info.get("rate"),
            "expected": info.get("expected", ""),
            "next_meeting": next_meeting,
            "days_until": days_until,
            "last_meeting": last_meeting,
            "upcoming_dates": upcoming[:4],  # Next 4 meetings
            "total_meetings_2025_2026": len(all_dates),
        })

    # Sort by days until next meeting
    result.sort(key=lambda x: x["days_until"] if x["days_until"] is not None else 9999)
    return result


# ── AI-Powered Query ────────────────────────────────────────────────────────

# ── Economic Calendar & Indicators ────────────────────────────────────────

@app.get("/api/economic-calendar")
def get_economic_calendar(
    country: str = None,
    start: str = None,
    end: str = None,
    impact: str = None,
):
    """Fetch upcoming economic events (CPI, NFP, GDP, rate decisions, etc.)."""
    return fetch_economic_calendar(country=country, start_date=start, end_date=end, impact=impact)


@app.get("/api/economic-indicators")
def get_indicators(country: str = None):
    """Fetch latest economic indicators for a country."""
    return fetch_economic_indicators(country=country)


# ── Macro Time Series (World Bank) ────────────────────────────────────────

@app.get("/api/macro/countries")
def macro_countries():
    """List countries with macro data. Paginates to get all distinct values."""
    client = get_client()
    seen = {}
    offset = 0
    page_size = 1000
    while True:
        result = (
            client.table("macro_timeseries")
            .select("country_code, country_name")
            .order("country_name")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        if not result.data:
            break
        for r in result.data:
            seen[r["country_code"]] = r["country_name"]
        if len(result.data) < page_size:
            break
        offset += page_size
    return [{"code": k, "name": v} for k, v in sorted(seen.items(), key=lambda x: x[1])]


@app.get("/api/macro/indicators")
def macro_indicators(country: str = None):
    """List available indicators, optionally for a specific country."""
    client = get_client()
    seen = {}
    offset = 0
    page_size = 1000
    while True:
        q = (
            client.table("macro_timeseries")
            .select("indicator_code, indicator_name, category, units")
            .range(offset, offset + page_size - 1)
        )
        if country:
            q = q.eq("country_code", country)
        result = q.execute()
        if not result.data:
            break
        for r in result.data:
            key = r["indicator_code"]
            if key not in seen:
                seen[key] = {
                    "code": key,
                    "name": r["indicator_name"],
                    "category": r.get("category", ""),
                    "units": r.get("units", ""),
                }
        if len(result.data) < page_size:
            break
        offset += page_size
    # Group by category
    by_cat = {}
    for ind in seen.values():
        cat = ind["category"] or "Other"
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(ind)
    return by_cat


@app.get("/api/macro/series")
def macro_series(country: str, indicator: str, start_year: int = 1960, end_year: int = 2026):
    """Fetch time series for one country + one indicator.

    Returns sorted array of {year, value} for charting.
    """
    client = get_client()
    result = (
        client.table("macro_timeseries")
        .select("year, value, indicator_name, category, units, country_name")
        .eq("country_code", country)
        .eq("indicator_code", indicator)
        .gte("year", start_year)
        .lte("year", end_year)
        .order("year")
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail=f"No data for {country}/{indicator}")

    meta = result.data[0]
    return {
        "country_code": country,
        "country_name": meta.get("country_name", country),
        "indicator_code": indicator,
        "indicator_name": meta.get("indicator_name", indicator),
        "category": meta.get("category", ""),
        "units": meta.get("units", ""),
        "data": [{"year": r["year"], "value": r["value"]} for r in result.data],
    }


@app.get("/api/macro/compare")
def macro_compare(indicator: str, countries: str, start_year: int = 1960, end_year: int = 2026):
    """Compare one indicator across multiple countries.

    countries is comma-separated (e.g., 'US,GB,DE,JP').
    """
    country_list = [c.strip() for c in countries.split(",")]
    client = get_client()
    result = (
        client.table("macro_timeseries")
        .select("country_code, country_name, year, value")
        .eq("indicator_code", indicator)
        .in_("country_code", country_list)
        .gte("year", start_year)
        .lte("year", end_year)
        .order("year")
        .execute()
    )
    # Group by country
    by_country = {}
    for r in result.data:
        cc = r["country_code"]
        if cc not in by_country:
            by_country[cc] = {"country_code": cc, "country_name": r["country_name"], "data": []}
        by_country[cc]["data"].append({"year": r["year"], "value": r["value"]})

    return list(by_country.values())


# ── Market Overview (for Koyfin dashboard) ─────────────────────────────────

@app.get("/api/market-overview")
async def market_overview():
    """Fetch real-time market overview: indices, sectors, FX, commodities, yields.

    Uses MarketData.app for US equities/ETFs + EODHD for indices/FX/crypto/bonds.
    Cached server-side for 60 seconds.
    """
    return await get_market_overview()


# ── Live Economic Calendar (FinanceFlowAPI) ────────────────────────────────

import time as _time
_cal_cache: dict = {"data": None, "ts": 0}
_CAL_TTL = 1800  # 30 minutes


@app.get("/api/calendar/live")
async def live_calendar(country: str = None):
    """Fetch live economic calendar from FinanceFlowAPI.

    If no country specified, fetches top 25 countries.
    Cached for 30 min to stay within 200 req/day limit.
    """
    TOP_COUNTRIES = [
        "United States", "United Kingdom", "Germany", "France", "Japan", "China",
        "Canada", "Australia", "Italy", "Spain", "Switzerland", "South Korea",
        "India", "Brazil", "Mexico", "Netherlands", "Sweden", "Norway",
        "Singapore", "Hong Kong", "New Zealand", "Belgium", "Austria",
        "Denmark", "Finland",
    ]
    now = _time.time()

    if country:
        # Single country fetch
        cache_key = f"cal_{country}"
        if _cal_cache.get(cache_key) and (now - _cal_cache.get(f"{cache_key}_ts", 0)) < _CAL_TTL:
            return _cal_cache[cache_key]
        try:
            events = await get_financial_calendar(country=country)
            _cal_cache[cache_key] = events
            _cal_cache[f"{cache_key}_ts"] = now
            return events
        except Exception as e:
            if _cal_cache.get(cache_key):
                return _cal_cache[cache_key]
            return {"error": str(e), "events": []}
    else:
        # Global: fetch top countries, cached as a bundle
        cache_key = "cal_global"
        if _cal_cache.get(cache_key) and (now - _cal_cache.get(f"{cache_key}_ts", 0)) < _CAL_TTL:
            return _cal_cache[cache_key]
        try:
            import asyncio as _aio
            all_events = []
            # Fetch in batches of 5 to avoid overwhelming the API
            for i in range(0, len(TOP_COUNTRIES), 5):
                batch = TOP_COUNTRIES[i:i+5]
                results = await _aio.gather(
                    *[get_financial_calendar(country=c) for c in batch],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, list):
                        all_events.extend(r)
                if i + 5 < len(TOP_COUNTRIES):
                    await _aio.sleep(0.5)
            # Sort by datetime
            all_events.sort(key=lambda e: e.get("datetime") or e.get("report_date") or "")
            # Filter to only high/moderate impact + next 14 days to keep response manageable
            from datetime import datetime as _dt, timedelta as _td
            cutoff = (_dt.now() + _td(days=14)).isoformat()
            filtered = [e for e in all_events if (e.get("datetime") or e.get("report_date") or "") <= cutoff]
            # Limit to high/moderate impact events to keep it digestible
            priority = [e for e in filtered if (e.get("economic_impact") or "").lower() in ("high", "moderate")]
            result = priority if len(priority) > 10 else filtered[:200]
            _cal_cache[cache_key] = result
            _cal_cache[f"{cache_key}_ts"] = now
            return result
        except Exception as e:
            if _cal_cache.get(cache_key):
                return _cal_cache[cache_key]
            return {"error": str(e), "events": []}


# ── MDA Candle Proxy (IP-locked token requires server-side calls) ───────────

import httpx as _httpx

_MDA_BASE = "https://api.marketdata.app/v1"
_MDA_TOKEN = "d2E2NDEybGtwZTBabnhSV2pkeEZBb3JfWW9uOHpKNnNIRTJ2bzNYZVlMcz0"
_candle_cache: dict = {}
_CANDLE_TTL = 300  # 5 minutes for intraday

@app.get("/api/candles/{resolution}/{ticker}")
async def proxy_candles(resolution: str, ticker: str, countback: int = None, start: str = None, end: str = None):
    """Proxy MDA candle requests through the server (IP-locked token)."""
    now = _time.time()
    cache_key = f"candle_{resolution}_{ticker}_{countback}_{start}_{end}"
    if _candle_cache.get(cache_key) and (now - _candle_cache.get(f"{cache_key}_ts", 0)) < _CANDLE_TTL:
        return _candle_cache[cache_key]

    params = {"token": _MDA_TOKEN, "format": "json"}
    if countback:
        params["countback"] = countback
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    url = f"{_MDA_BASE}/stocks/candles/{resolution}/{ticker}/"
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            data = r.json()
            if data.get("s") == "ok":
                _candle_cache[cache_key] = data
                _candle_cache[f"{cache_key}_ts"] = now
            return data
    except Exception as e:
        if _candle_cache.get(cache_key):
            return _candle_cache[cache_key]
        return {"s": "error", "errmsg": str(e)}


# ── Generic MDA proxy (so frontend never hits MDA directly → preserves IP lock) ──

_mda_generic_cache: dict = {}
_MDA_GENERIC_TTL = 60  # 1 minute cache


@app.get("/api/mda/{path:path}")
async def proxy_mda(path: str, request: Request):
    """Proxy any MarketData.app request through the server.

    This ensures the MDA token is only ever used from the server IP,
    preventing IP-lock issues on MDA's single-device plans.
    """
    now = _time.time()
    query_string = str(request.query_params)
    cache_key = f"mda_{path}_{query_string}"
    if _mda_generic_cache.get(cache_key) and (now - _mda_generic_cache.get(f"{cache_key}_ts", 0)) < _MDA_GENERIC_TTL:
        return _mda_generic_cache[cache_key]

    # Forward all query params, inject token
    params = dict(request.query_params)
    params["token"] = _MDA_TOKEN
    if "format" not in params:
        params["format"] = "json"

    url = f"{_MDA_BASE}/{path}"
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            data = r.json()
            if data.get("s") == "ok":
                _mda_generic_cache[cache_key] = data
                _mda_generic_cache[f"{cache_key}_ts"] = now
            return data
    except Exception as e:
        if _mda_generic_cache.get(cache_key):
            return _mda_generic_cache[cache_key]
        return {"s": "error", "errmsg": str(e)}


# ── Positions (from Supabase state table for market dashboard) ─────────────

@app.get("/api/positions/open")
def get_open_positions():
    """Fetch open positions from the Supabase state table.

    Trades are stored as JSON in state.data.trades[]. We filter for status='Open'.
    """
    client = get_client()
    try:
        result = (
            client.table("state")
            .select("data")
            .eq("id", "main")
            .execute()
        )
        if not result.data or not result.data[0].get("data"):
            return []
        state_data = result.data[0]["data"]
        trades = state_data.get("trades", [])
        open_trades = [t for t in trades if t.get("status") == "Open"]
        # Return only the fields needed for the dashboard
        return [
            {
                "id": t.get("id"),
                "ticker": t.get("ticker", ""),
                "tradeType": t.get("tradeType", ""),
                "assetClass": t.get("assetClass", ""),
                "entryDate": t.get("entryDate", ""),
                "contracts": t.get("contracts"),
                "shortStrike": t.get("shortStrike"),
                "longStrike": t.get("longStrike"),
                "expiry": t.get("expiry"),
                "premiumCollected": t.get("premiumCollected"),
                "tpPrice": t.get("tpPrice"),
                "slPrice": t.get("slPrice"),
                "spreadTPPct": t.get("spreadTPPct"),
                "spreadSLPct": t.get("spreadSLPct"),
                "sector": t.get("sector", ""),
                "thesis": t.get("thesis", ""),
                "dteAtEntry": t.get("dteAtEntry"),
                "pop": t.get("pop"),
            }
            for t in open_trades
        ]
    except Exception as e:
        return []


@app.get("/api/watchlist")
def get_watchlist():
    """Fetch the watchlist + related data (targets, notes, S/R) from state table."""
    client = get_client()
    try:
        result = (
            client.table("state")
            .select("data")
            .eq("id", "main")
            .execute()
        )
        if not result.data or not result.data[0].get("data"):
            return {"tickers": [], "targets": {}, "notes": {}, "srLevels": {}}
        state_data = result.data[0]["data"]
        return {
            "tickers": state_data.get("watchlist", []),
            "targets": state_data.get("watchlistTargets", {}),
            "notes": state_data.get("watchlistNotes", {}),
            "srLevels": state_data.get("srLevels", {}),
            "sectors": state_data.get("sectors", {}),
        }
    except Exception:
        return {"tickers": [], "targets": {}, "notes": {}, "srLevels": {}}


@app.post("/api/ask")
def ask_ai(req: AskRequest):
    """Ask a natural-language question and get backtest results.

    Example questions:
      - "Which US equities go up in the week following a 0.25% FOMC rate cut?"
      - "What happens to gold when VIX is above 30?"
      - "Show me SPY behaviour after 3 consecutive down days"
    """
    result = ask_scanner(req.question)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Optionally generate AI explanation
    if req.explain and "results" in result:
        try:
            result["explanation"] = explain_results(req.question, result)
        except Exception as e:
            result["explanation"] = f"Could not generate explanation: {e}"

    return result
