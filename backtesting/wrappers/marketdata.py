from __future__ import annotations

"""Marketdata.app API wrapper — US equities and ETFs."""
import asyncio
import httpx
from datetime import date, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential

from backtesting.config import MARKETDATA_API_TOKEN

BASE = "https://api.marketdata.app/v1"
HEADERS = {"Authorization": f"Bearer {MARKETDATA_API_TOKEN}"}
_semaphore = asyncio.Semaphore(20)  # max concurrent requests (Trader plan: 100k daily credits)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=30)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_candles(
    symbol: str,
    from_date: str = "2000-01-01",
    to_date: str | None = None,
    resolution: str = "D",
) -> list[dict]:
    """Fetch daily OHLCV candles for a US equity/ETF.

    Returns list of dicts with keys: date, open, high, low, close, volume.
    """
    if to_date is None:
        to_date = date.today().isoformat()

    rows = []
    async with _semaphore:
        async with _client() as c:
            # Marketdata.app caps at 5 years per request — paginate
            start = date.fromisoformat(from_date)
            end = date.fromisoformat(to_date)
            while start < end:
                chunk_end = min(start + timedelta(days=5 * 365), end)
                resp = await c.get(
                    f"{BASE}/stocks/candles/{resolution}/{symbol}/",
                    params={
                        "from": start.isoformat(),
                        "to": chunk_end.isoformat(),
                        "adjusted": "true",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("s") == "ok":
                        for i, t in enumerate(data["t"]):
                            rows.append(
                                {
                                    "ticker": symbol,
                                    "date": date.fromtimestamp(t).isoformat(),
                                    "open": data["o"][i],
                                    "high": data["h"][i],
                                    "low": data["l"][i],
                                    "close": data["c"][i],
                                    "volume": int(data["v"][i]) if data["v"][i] else None,
                                    "asset_class": "equity",
                                    "source": "marketdata",
                                    "adjusted_flag": True,
                                }
                            )
                elif resp.status_code == 429:
                    await asyncio.sleep(5)
                    continue
                start = chunk_end + timedelta(days=1)
    return rows


async def get_earnings_dates(symbol: str, from_date: str = "2000-01-01") -> list[dict]:
    """Fetch historical earnings report dates for a symbol.

    Returns list of dicts: {ticker, date, fiscal_year, fiscal_quarter, eps_actual, eps_estimate}.
    Uses reportDate (when earnings were actually announced) for accurate exclusion.
    Paginates automatically since Marketdata.app caps at 12 entries per request.
    """
    all_rows = []
    current_from = from_date

    async with _semaphore:
        async with _client() as c:
            for _ in range(20):  # Safety: max 20 pages (~60 years of quarterly data)
                resp = await c.get(
                    f"{BASE}/stocks/earnings/{symbol}/",
                    params={"from": current_from},
                )
                if resp.status_code in (200, 203):
                    data = resp.json()
                    if data.get("s") == "ok" and data.get("reportDate"):
                        page_rows = []
                        for i in range(len(data["reportDate"])):
                            rd = data["reportDate"][i]
                            if rd:
                                page_rows.append({
                                    "ticker": symbol,
                                    "date": date.fromtimestamp(rd).isoformat(),
                                    "fiscal_year": data.get("fiscalYear", [None])[i],
                                    "fiscal_quarter": data.get("fiscalQuarter", [None])[i],
                                    "eps_actual": data.get("reportedEPS", [None])[i],
                                    "eps_estimate": data.get("estimatedEPS", [None])[i],
                                })
                        if not page_rows:
                            break
                        all_rows.extend(page_rows)
                        # Next page starts day after last reportDate
                        last_date = max(r["date"] for r in page_rows)
                        next_from = (date.fromisoformat(last_date) + timedelta(days=1)).isoformat()
                        if next_from <= current_from or next_from > date.today().isoformat():
                            break
                        current_from = next_from
                    else:
                        break
                elif resp.status_code == 429:
                    await asyncio.sleep(5)
                    continue
                else:
                    break

    return all_rows


async def screen_us_equities(min_market_cap: float = 500_000_000, min_avg_volume: int = 500_000) -> list[str]:
    """Screen for US equities with market cap > threshold and avg volume > threshold.

    Falls back to a curated universe if the screener endpoint is unavailable.
    """
    async with _client() as c:
        # Try screener endpoint
        resp = await c.get(
            f"{BASE}/stocks/screener/",
            params={
                "market_cap_gte": int(min_market_cap),
                "volume_gte": min_avg_volume,
                "limit": 5000,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("s") == "ok" and data.get("symbol"):
                return data["symbol"]

    # Fallback: fetch S&P 500 + Russell 1000 constituent tickers from EODHD or static list
    return await _fallback_universe()


async def _fallback_universe() -> list[str]:
    """If screener fails, pull a broad universe from the bulks endpoint."""
    async with _client() as c:
        resp = await c.get(f"{BASE}/stocks/bulks/quotes/", params={"snapshot": "true"})
        if resp.status_code == 200:
            data = resp.json()
            if data.get("s") == "ok" and data.get("symbol"):
                symbols = []
                for i, sym in enumerate(data["symbol"]):
                    mc = data.get("marketCap", [None])[i] if "marketCap" in data else None
                    vol = data.get("volume", [0])[i] if "volume" in data else 0
                    if mc and mc >= 500_000_000 and vol and vol >= 500_000:
                        symbols.append(sym)
                return symbols
    return []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_options_chain(symbol: str, expiration: str = None) -> dict:
    """Fetch options chain for a symbol."""
    params = {}
    if expiration:
        params["expiration"] = expiration
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/options/chain/{symbol}/", params=params)
            if resp.status_code == 200:
                return resp.json()
    return {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_quote(symbol: str) -> dict | None:
    """Fetch real-time quote."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(f"{BASE}/stocks/quotes/{symbol}/")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("s") == "ok":
                    return {
                        "symbol": data["symbol"][0],
                        "last": data["last"][0],
                        "change": data["change"][0],
                        "changepct": data["changepct"][0],
                        "volume": data["volume"][0],
                    }
    return None
