"""Market overview data — MarketData.app (US equities/ETFs) + EODHD (FX/bonds/indices/crypto).

Single endpoint fetches all dashboard data, caches for 60s server-side.
No Yahoo Finance. No bulk extraction. Uses the APIs we're paying for.
"""
from __future__ import annotations

import asyncio
import time
import httpx

from backtesting.config import MARKETDATA_API_TOKEN, EODHD_API_TOKEN

MDA_BASE = "https://api.marketdata.app/v1"
MDA_HEADERS = {"Authorization": f"Bearer {MARKETDATA_API_TOKEN}"}

EODHD_BASE = "https://eodhd.com/api"

# ── Cache ────────────────────────────────────────────────────────────────────
_overview_cache: dict = {"data": None, "ts": 0}
CACHE_TTL = 60  # seconds


# ── Symbol definitions ───────────────────────────────────────────────────────

# MDA_QUOTES no longer used — all quotes now via EODHD to avoid IP-lock issues
MDA_QUOTES = {}

# EODHD real-time — indices, FX, crypto, sectors, global ETFs
EODHD_QUOTES = {
    "sectors": [
        {"s": "XLU.US",  "ticker": "XLU",  "name": "Utilities"},
        {"s": "XLP.US",  "ticker": "XLP",  "name": "Cons. Staples"},
        {"s": "XLE.US",  "ticker": "XLE",  "name": "Energy"},
        {"s": "XLRE.US", "ticker": "XLRE", "name": "Real Estate"},
        {"s": "XLF.US",  "ticker": "XLF",  "name": "Financials"},
        {"s": "XLV.US",  "ticker": "XLV",  "name": "Health Care"},
        {"s": "XLI.US",  "ticker": "XLI",  "name": "Industrials"},
        {"s": "XLY.US",  "ticker": "XLY",  "name": "Cons. Discretionary"},
        {"s": "XLC.US",  "ticker": "XLC",  "name": "Communications"},
        {"s": "XLK.US",  "ticker": "XLK",  "name": "Technology"},
        {"s": "XLB.US",  "ticker": "XLB",  "name": "Materials"},
    ],
    "globalBroad": [
        {"s": "EEM.US",  "ticker": "EEM",  "name": "Emerging"},
        {"s": "ACWI.US", "ticker": "ACWI", "name": "Developed Blend"},
        {"s": "EFA.US",  "ticker": "EFA",  "name": "Developed"},
    ],
    "globalDeveloped": [
        {"s": "EWJ.US", "ticker": "EWJ", "name": "Japan"},
        {"s": "EWU.US", "ticker": "EWU", "name": "United Kingdom"},
        {"s": "EWG.US", "ticker": "EWG", "name": "Germany"},
        {"s": "EWA.US", "ticker": "EWA", "name": "Australia"},
        {"s": "EWQ.US", "ticker": "EWQ", "name": "France"},
    ],
    "globalEmerging": [
        {"s": "EWY.US", "ticker": "EWY", "name": "South Korea"},
        {"s": "FXI.US", "ticker": "FXI", "name": "China"},
        {"s": "EWW.US", "ticker": "EWW", "name": "Mexico"},
        {"s": "EPI.US", "ticker": "EPI", "name": "India"},
        {"s": "EWZ.US", "ticker": "EWZ", "name": "Brazil"},
        {"s": "EZA.US", "ticker": "EZA", "name": "South Africa"},
    ],
    "indices": [
        {"s": "GSPC.INDX",  "ticker": "SPX",  "name": "S&P 500"},
        {"s": "DJI.INDX",   "ticker": "INDU", "name": "Dow Jones"},
        {"s": "IXIC.INDX",  "ticker": "NDX",  "name": "Nasdaq"},
        {"s": "RUT.INDX",   "ticker": "RTY",  "name": "Russell 2000"},
    ],
    "vix": [
        {"s": "VIX.INDX",   "ticker": "VIX",  "name": "CBOE VIX"},
    ],
    "currencies": [
        {"s": "USDJPY.FOREX", "ticker": "USDJPY", "name": "Japanese Yen"},
        {"s": "EURUSD.FOREX", "ticker": "EURUSD", "name": "Euro"},
        {"s": "GBPUSD.FOREX", "ticker": "GBPUSD", "name": "British Pound"},
        {"s": "USDCAD.FOREX", "ticker": "USDCAD", "name": "Canadian Dollar"},
    ],
    "crypto": [
        {"s": "BTC-USD.CC", "ticker": "BTCUSD", "name": "Bitcoin"},
    ],
    "commodities": [
        {"s": "GLD.US",  "ticker": "XAUUSD", "name": "Gold"},
        {"s": "USO.US",  "ticker": "CL1",    "name": "Crude Oil"},
        {"s": "BNO.US",  "ticker": "CO1",    "name": "Brent Crude"},
        {"s": "UNG.US",  "ticker": "NG1",    "name": "Natural Gas"},
    ],
}

# EODHD bond yields — {country: [(eodhd_symbol, maturity_label), ...]}
YIELD_SYMBOLS = {
    "United States": [
        ("US1Y.GBOND", "1Y"), ("US5Y.GBOND", "5Y"),
        ("US10Y.GBOND", "10Y"), ("US30Y.GBOND", "30Y"),
    ],
    "Germany": [
        ("DE2Y.GBOND", "1Y"), ("DE5Y.GBOND", "5Y"),
        ("DE10Y.GBOND", "10Y"), ("DE30Y.GBOND", "30Y"),
    ],
    "United Kingdom": [
        ("UK2Y.GBOND", "1Y"), ("UK5Y.GBOND", "5Y"),
        ("UK10Y.GBOND", "10Y"), ("UK30Y.GBOND", "30Y"),
    ],
    "Japan": [
        ("JP2Y.GBOND", "1Y"), ("JP5Y.GBOND", "5Y"),
        ("JP10Y.GBOND", "10Y"), ("JP30Y.GBOND", "30Y"),
    ],
    "China": [
        ("CN2Y.GBOND", "1Y"), ("CN5Y.GBOND", "5Y"),
        ("CN10Y.GBOND", "10Y"), ("CN30Y.GBOND", "30Y"),
    ],
    "Italy": [
        ("IT2Y.GBOND", "1Y"), ("IT5Y.GBOND", "5Y"),
        ("IT10Y.GBOND", "10Y"), ("IT30Y.GBOND", "30Y"),
    ],
    "Spain": [
        ("ES10Y.GBOND", "10Y"),
    ],
}


# ── Fetchers ─────────────────────────────────────────────────────────────────

_sem = asyncio.Semaphore(20)


async def _mda_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Fetch one quote from MarketData.app."""
    async with _sem:
        try:
            r = await client.get(f"{MDA_BASE}/stocks/quotes/{symbol}/")
            if r.status_code == 200:
                d = r.json()
                if d.get("s") == "ok":
                    return {
                        "price": round(d["last"][0], 2),
                        "change": round(d["change"][0] if d.get("change") else 0, 2),
                        "changePct": round(d["changepct"][0] if d.get("changepct") else 0, 2),
                    }
        except Exception:
            pass
    return None


async def _eodhd_rt(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Fetch one real-time quote from EODHD."""
    async with _sem:
        try:
            r = await client.get(
                f"{EODHD_BASE}/real-time/{symbol}",
                params={"api_token": EODHD_API_TOKEN, "fmt": "json"},
            )
            if r.status_code == 200:
                d = r.json()
                return {
                    "price": round(float(d.get("close", 0)), 4),
                    "change": round(float(d.get("change", 0)), 4),
                    "changePct": round(float(d.get("change_p", 0)), 2),
                }
        except Exception:
            pass
    return None


async def _fetch_mda_section(client: httpx.AsyncClient, items: list[dict]) -> list[dict]:
    """Fetch all MarketData.app quotes for a section in parallel."""
    tasks = [_mda_quote(client, item["s"]) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for item, result in zip(items, results):
        q = result if isinstance(result, dict) else None
        out.append({
            "name": item["name"],
            "ticker": item["s"],
            "price": q["price"] if q else 0,
            "change": q["change"] if q else 0,
            "changePct": q["changePct"] if q else 0,
        })
    return out


async def _fetch_eodhd_section(client: httpx.AsyncClient, items: list[dict]) -> list[dict]:
    """Fetch all EODHD real-time quotes for a section in parallel."""
    tasks = [_eodhd_rt(client, item["s"]) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for item, result in zip(items, results):
        q = result if isinstance(result, dict) else None
        out.append({
            "name": item["name"],
            "ticker": item.get("ticker", item["s"]),
            "price": q["price"] if q else 0,
            "change": q["change"] if q else 0,
            "changePct": q["changePct"] if q else 0,
        })
    return out


async def _fetch_yields(client: httpx.AsyncClient) -> list[dict]:
    """Fetch global bond yields from EODHD GBOND exchange."""
    # Collect all yield symbols
    all_items = []
    for country, bonds in YIELD_SYMBOLS.items():
        for eodhd_sym, maturity in bonds:
            all_items.append({"country": country, "s": eodhd_sym, "maturity": maturity})

    tasks = [_eodhd_rt(client, item["s"]) for item in all_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Group by country
    by_country: dict[str, dict] = {}
    for item, result in zip(all_items, results):
        c = item["country"]
        if c not in by_country:
            by_country[c] = {"country": c, "flag": _flag(c)}
        q = result if isinstance(result, dict) else None
        by_country[c][item["maturity"]] = f"{q['price']:.3f}%" if q and q["price"] else "—"

    # Preserve order
    return [by_country[c] for c in YIELD_SYMBOLS if c in by_country]


def _flag(country: str) -> str:
    """Country name → flag emoji."""
    flags = {
        "United States": "🇺🇸", "Germany": "🇩🇪", "United Kingdom": "🇬🇧",
        "Japan": "🇯🇵", "China": "🇨🇳", "Italy": "🇮🇹", "Spain": "🇪🇸",
    }
    return flags.get(country, "")


# ── Main aggregator ──────────────────────────────────────────────────────────

async def get_market_overview() -> dict:
    """Fetch full market overview. Cached for 60 seconds."""
    now = time.time()
    if _overview_cache["data"] and (now - _overview_cache["ts"]) < CACHE_TTL:
        return _overview_cache["data"]

    async with httpx.AsyncClient(timeout=15) as eodhd_client:
        # All quotes now via EODHD — no MDA IP-lock issues
        (
            sectors,
            global_broad,
            global_developed,
            global_emerging,
            indices,
            vix,
            currencies,
            crypto,
            commodities,
            yields,
        ) = await asyncio.gather(
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["sectors"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["globalBroad"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["globalDeveloped"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["globalEmerging"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["indices"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["vix"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["currencies"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["crypto"]),
            _fetch_eodhd_section(eodhd_client, EODHD_QUOTES["commodities"]),
            _fetch_yields(eodhd_client),
        )

    result = {
        "indices": indices,
        "vix": vix,
        "sectors": sectors,
        "currencies": currencies + crypto,
        "commodities": commodities,
        "globalBroad": global_broad,
        "globalDeveloped": global_developed,
        "globalEmerging": global_emerging,
        "yields": yields,
        "ts": int(time.time()),
    }

    _overview_cache["data"] = result
    _overview_cache["ts"] = now
    return result
