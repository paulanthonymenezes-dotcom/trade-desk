from __future__ import annotations

"""FinanceFlowAPI wrapper — bond yields, economic calendar, world indicators."""
import asyncio
import httpx
from datetime import date, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential

from backtesting.config import FINANCEFLOW_API_TOKEN

BASE = "https://financeflowapi.com/api/v1"
_semaphore = asyncio.Semaphore(10)  # respect rate limits


def _params(**extra) -> dict:
    return {"api_key": FINANCEFLOW_API_TOKEN, **extra}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


# ── Bond symbol mappings ─────────────────────────────────────────────────────
# country -> {bond_type -> display_ticker}
BOND_UNIVERSE = {
    "United States": {
        "Bond 2Y": "US-2Y",
        "Bond 5Y": "US-5Y",
        "Bond 10Y": "US-10Y",
        "Bond 30Y": "US-30Y",
    },
    "United Kingdom": {
        "Bond 2Y": "UK-2Y",
        "Bond 10Y": "UK-10Y",
        "Bond 30Y": "UK-30Y",
    },
    "Germany": {
        "Bond 2Y": "DE-2Y",
        "Bond 10Y": "DE-10Y",
        "Bond 30Y": "DE-30Y",
    },
    "Japan": {
        "Bond 2Y": "JP-2Y",
        "Bond 10Y": "JP-10Y",
        "Bond 30Y": "JP-30Y",
    },
    "Canada": {
        "Bond 2Y": "CA-2Y",
        "Bond 10Y": "CA-10Y",
    },
    "Australia": {
        "Bond 2Y": "AU-2Y",
        "Bond 10Y": "AU-10Y",
    },
    "China": {
        "Bond 2Y": "CN-2Y",
        "Bond 10Y": "CN-10Y",
    },
    "Brazil": {
        "Bond 2Y": "BR-2Y",
        "Bond 10Y": "BR-10Y",
    },
}


# ── Bond yield candles ────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_bond_candles(
    country: str,
    bond_type: str,
    display_ticker: str,
    from_date: str = "2000-01-01",
    to_date: str | None = None,
) -> list[dict]:
    """Fetch historical yield candles for a government bond.

    Returns list of dicts ready for ohlcv_daily upsert.
    Yield values are stored as the OHLC prices (they represent yield %).
    """
    if to_date is None:
        to_date = date.today().isoformat()

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/bonds-history-candles",
                params=_params(
                    country=country,
                    type=bond_type,
                    date_from=from_date,
                    date_to=to_date,
                ),
            )
            if resp.status_code != 200:
                print(f"    Bond candles error {resp.status_code}: {resp.text[:200]}")
                return []
            data = resp.json()

    # Response can be a list of candle dicts or wrapped in a key
    candles = data if isinstance(data, list) else data.get("data", data.get("results", []))
    if not isinstance(candles, list):
        return []

    rows = []
    for bar in candles:
        if not bar.get("close") and not bar.get("yield"):
            continue
        rows.append(
            {
                "ticker": display_ticker,
                "date": bar.get("date", ""),
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close") or bar.get("yield"),
                "volume": None,  # bonds don't have volume
                "asset_class": "bond",
                "source": "financeflow",
                "adjusted_flag": True,
            }
        )
    return rows


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_bond_catalog() -> list[dict]:
    """Fetch list of countries with available bond data."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/bonds-catalog", params=_params())
            if resp.status_code == 200:
                return resp.json() if isinstance(resp.json(), list) else []
    return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_bond_spot(country: str) -> list[dict]:
    """Fetch real-time bond yields for a country."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/bonds-spot",
                params=_params(country=country),
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    return []


# ── Financial Calendar ────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_financial_calendar(
    country: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch upcoming economic events from the financial calendar.

    Max 60-day window per request. Returns events with actual/previous/consensus.
    """
    if from_date is None:
        from_date = date.today().isoformat()
    if to_date is None:
        to_date = (date.today() + timedelta(days=60)).isoformat()

    params = _params(date_from=from_date, date_to=to_date)
    if country:
        params["country"] = country

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/financial-calendar", params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()

    events = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(events, list):
        return []

    rows = []
    for ev in events:
        rows.append(
            {
                "country": ev.get("country", ""),
                "report_name": ev.get("report_name", ""),
                "report_date": ev.get("report_date", ev.get("date", "")),
                "datetime": ev.get("datetime"),
                "actual": ev.get("actual"),
                "previous": ev.get("previous"),
                "consensus": ev.get("consensus"),
                "economic_impact": ev.get("economicImpact", ev.get("economic_impact", "")),
                "source": "financeflow",
            }
        )
    return rows


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_calendar_catalog() -> list[dict]:
    """Fetch list of countries with calendar data."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/calendar-catalog", params=_params())
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
    return []


# ── World Economic Indicators ─────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_economic_indicators(
    country: str,
    indicator_name: str | None = None,
) -> list[dict]:
    """Fetch economic indicators for a country.

    If indicator_name is None, returns all available indicators for that country.
    """
    params = _params(country=country)
    if indicator_name:
        params["indicator_name"] = indicator_name

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/world-indicators", params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()

    indicators = data if isinstance(data, list) else data.get("data", [data] if isinstance(data, dict) else [])
    if not isinstance(indicators, list):
        return []

    rows = []
    for ind in indicators:
        rows.append(
            {
                "country": ind.get("country", country),
                "indicator_name": ind.get("indicator_name", ""),
                "last_value": _safe_float(ind.get("last")),
                "previous_value": _safe_float(ind.get("previous")),
                "units": ind.get("units", ""),
                "report_date": ind.get("report_date"),
                "source": "financeflow",
            }
        )
    return rows


# ── Commodity spot (supplementary — EODHD is primary for commodity OHLCV) ────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_commodity_spot(name: str) -> dict | None:
    """Fetch real-time commodity price (for dashboard display, not backtesting)."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/commodity-spot",
                params=_params(name=name),
            )
            if resp.status_code == 200:
                return resp.json()
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_commodity_catalog() -> list[dict]:
    """Fetch list of available commodities."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/commodity-catalog", params=_params())
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
    return []


# ── Index spot (real-time only — EODHD is primary for index OHLCV) ────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_index_spot(country: str | None = None, benchmark: str | None = None) -> list[dict]:
    """Fetch real-time index data."""
    params = _params()
    if country:
        params["country"] = country
    if benchmark:
        params["benchmark"] = benchmark

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/index-spot", params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    return []


# ── Bulk fetchers ─────────────────────────────────────────────────────────────

async def fetch_all_bond_candles(from_date: str = "2000-01-01") -> list[dict]:
    """Fetch bond yield candles for the entire BOND_UNIVERSE.

    Iterates sequentially with rate-limit pauses to stay within plan limits.
    """
    all_rows = []
    for country, bonds in BOND_UNIVERSE.items():
        for bond_type, display_ticker in bonds.items():
            print(f"  Fetching {display_ticker} ({country} {bond_type})...")
            try:
                rows = await get_bond_candles(
                    country=country,
                    bond_type=bond_type,
                    display_ticker=display_ticker,
                    from_date=from_date,
                )
                all_rows.extend(rows)
                if rows:
                    print(f"    {len(rows)} rows (first: {rows[0]['date']}, last: {rows[-1]['date']})")
                else:
                    print(f"    No data returned")
            except Exception as e:
                print(f"    Error: {e}")

            await asyncio.sleep(1)  # rate limit courtesy

    return all_rows


async def fetch_economic_calendar_range(
    countries: list[str] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch economic calendar for multiple countries.

    Handles the 60-day window limit by paginating.
    """
    if from_date is None:
        from_date = date.today().isoformat()
    if to_date is None:
        to_date = (date.today() + timedelta(days=60)).isoformat()

    # If no countries specified, fetch global
    if not countries:
        countries = [None]  # None = all countries

    all_events = []
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)

    for country in countries:
        # Paginate in 60-day chunks
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=59), end)
            try:
                events = await get_financial_calendar(
                    country=country,
                    from_date=chunk_start.isoformat(),
                    to_date=chunk_end.isoformat(),
                )
                all_events.extend(events)
            except Exception as e:
                label = country or "Global"
                print(f"  Calendar error ({label}): {e}")

            chunk_start = chunk_end + timedelta(days=1)
            await asyncio.sleep(0.5)

    return all_events


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None
