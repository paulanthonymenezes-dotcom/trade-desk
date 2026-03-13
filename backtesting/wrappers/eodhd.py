from __future__ import annotations

"""EODHD API wrapper — FX, crypto, global indices, bonds.

Commodities use ETF proxies (GLD, USO, etc.) via the equities wrapper.
Direct commodity futures (.COMM) require a higher-tier EODHD plan.
Bond yields use the GBOND exchange (119 symbols, 25+ countries).
"""
import asyncio
import httpx
from datetime import date
from tenacity import retry, stop_after_attempt, wait_exponential

from backtesting.config import EODHD_API_TOKEN

BASE = "https://eodhd.com/api"
_semaphore = asyncio.Semaphore(15)


def _params(**extra) -> dict:
    return {"api_token": EODHD_API_TOKEN, "fmt": "json", **extra}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


# ── Symbol mappings ──────────────────────────────────────────────────────────

FX_PAIRS = {
    "EURUSD": "EURUSD.FOREX",
    "GBPUSD": "GBPUSD.FOREX",
    "USDJPY": "USDJPY.FOREX",
    "USDKRW": "USDKRW.FOREX",
    "USDCNY": "USDCNY.FOREX",
    "USDCAD": "USDCAD.FOREX",
    "AUDUSD": "AUDUSD.FOREX",
    "EURJPY": "EURJPY.FOREX",
    "AUDJPY": "AUDJPY.FOREX",
    "USDCHF": "USDCHF.FOREX",
    "NZDUSD": "NZDUSD.FOREX",
    "USDMXN": "USDMXN.FOREX",
}

CRYPTO = {
    "BTC": "BTC-USD.CC",
    "ETH": "ETH-USD.CC",
    "SOL": "SOL-USD.CC",
    "XRP": "XRP-USD.CC",
    "ADA": "ADA-USD.CC",
    "DOGE": "DOGE-USD.CC",
    "AVAX": "AVAX-USD.CC",
    "LINK": "LINK-USD.CC",
    "DOT": "DOT-USD.CC",
    "MATIC": "MATIC-USD.CC",
}

INDICES = {
    "NIKKEI": "N225.INDX",
    "KOSPI": "KOSPI.INDX",
    "HANGSENG": "HSI.INDX",
    "SHANGHAI": "SSEC.INDX",
    "DAX": "GDAXI.INDX",
    "FTSE100": "FTSE.INDX",
    "CAC40": "FCHI.INDX",
    "SP500": "GSPC.INDX",
    "NASDAQ": "IXIC.INDX",
    "VIX": "VIX.INDX",
    "TSX": "GSPTSE.INDX",
}

# Commodity ETF proxies — direct futures (.COMM) require a higher EODHD tier.
# These ETFs closely track the underlying commodity and are tradeable.
COMMODITY_ETFS = {
    "GOLD": "GLD.US",
    "SILVER": "SLV.US",
    "CRUDE_OIL": "USO.US",
    "NATURAL_GAS": "UNG.US",
    "COPPER": "COPX.US",
    "BRENT_OIL": "BNO.US",
    "AGRICULTURE": "DBA.US",
    "DXY": "UUP.US",  # Dollar Index proxy (Invesco DB USD Index Bullish Fund)
}

# Government bond yields via GBOND exchange
# display_ticker -> eodhd_symbol
BONDS = {
    # US full curve
    "US-1M":  "US1M.GBOND",
    "US-3M":  "US3M.GBOND",
    "US-6M":  "US6M.GBOND",
    "US-1Y":  "US1Y.GBOND",
    "US-2Y":  "US2Y.GBOND",
    "US-3Y":  "US3Y.GBOND",
    "US-5Y":  "US5Y.GBOND",
    "US-7Y":  "US7Y.GBOND",
    "US-10Y": "US10Y.GBOND",
    "US-20Y": "US20Y.GBOND",
    "US-30Y": "US30Y.GBOND",
    # UK
    "UK-3M":  "UK3M.GBOND",
    "UK-1Y":  "UK1Y.GBOND",
    "UK-2Y":  "UK2Y.GBOND",
    "UK-5Y":  "UK5Y.GBOND",
    "UK-10Y": "UK10Y.GBOND",
    "UK-30Y": "UK30Y.GBOND",
    # Germany
    "DE-3M":  "DE3M.GBOND",
    "DE-1Y":  "DE1Y.GBOND",
    "DE-2Y":  "DE2Y.GBOND",
    "DE-5Y":  "DE5Y.GBOND",
    "DE-10Y": "DE10Y.GBOND",
    "DE-30Y": "DE30Y.GBOND",
    # Japan
    "JP-3M":  "JP3M.GBOND",
    "JP-2Y":  "JP2Y.GBOND",
    "JP-5Y":  "JP5Y.GBOND",
    "JP-10Y": "JP10Y.GBOND",
    "JP-30Y": "JP30Y.GBOND",
    # Canada
    "CA-1Y":  "CA1Y.GBOND",
    "CA-2Y":  "CA2Y.GBOND",
    "CA-5Y":  "CA5Y.GBOND",
    "CA-10Y": "CA10Y.GBOND",
    "CA-30Y": "CA30Y.GBOND",
    # Australia
    "AU-2Y":  "AU2Y.GBOND",
    "AU-5Y":  "AU5Y.GBOND",
    "AU-10Y": "AU10Y.GBOND",
    "AU-30Y": "AU30Y.GBOND",
    # France
    "FR-2Y":  "FR2Y.GBOND",
    "FR-5Y":  "FR5Y.GBOND",
    "FR-10Y": "FR10Y.GBOND",
    # Italy
    "IT-2Y":  "IT2Y.GBOND",
    "IT-5Y":  "IT5Y.GBOND",
    "IT-10Y": "IT10Y.GBOND",
    "IT-30Y": "IT30Y.GBOND",
    # China
    "CN-2Y":  "CN2Y.GBOND",
    "CN-5Y":  "CN5Y.GBOND",
    "CN-10Y": "CN10Y.GBOND",
    "CN-30Y": "CN30Y.GBOND",
    # India
    "IN-2Y":  "IN2Y.GBOND",
    "IN-5Y":  "IN5Y.GBOND",
    "IN-10Y": "IN10Y.GBOND",
    # Brazil
    "BR-1Y":  "BR1Y.GBOND",
    # South Korea
    "KR-2Y":  "KR2Y.GBOND",
    "KR-5Y":  "KR5Y.GBOND",
    "KR-10Y": "KR10Y.GBOND",
    # Mexico
    "MX-10Y": "MX10Y.GBOND",
    # Switzerland
    "CH-10Y": "SW10Y.GBOND",
    # Spain
    "ES-10Y": "ES10Y.GBOND",
    # Norway
    "NO-10Y": "NO10Y.GBOND",
    # Sweden
    "SE-10Y": "SE10Y.GBOND",
    # South Africa (Kenya available too, but less relevant)
    # Singapore
    "SG-10Y": "SG10Y.GBOND",
    # Indonesia
    "ID-10Y": "ID10Y.GBOND",
    # Netherlands
    "NL-10Y": "NL10Y.GBOND",
    # New Zealand
    "NZ-10Y": "NZ10Y.GBOND",
}

# Unified map: display_ticker -> (eodhd_symbol, asset_class)
ALL_EODHD = {}
for k, v in FX_PAIRS.items():
    ALL_EODHD[k] = (v, "fx")
for k, v in CRYPTO.items():
    ALL_EODHD[k] = (v, "crypto")
for k, v in INDICES.items():
    ALL_EODHD[k] = (v, "index")
for k, v in COMMODITY_ETFS.items():
    ALL_EODHD[k] = (v, "commodity")
for k, v in BONDS.items():
    ALL_EODHD[k] = (v, "bond")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_eod(
    eodhd_symbol: str,
    display_ticker: str,
    asset_class: str,
    from_date: str = "1993-01-01",
    to_date: str | None = None,
) -> list[dict]:
    """Fetch daily EOD data from EODHD.

    Returns list of dicts ready for ohlcv_daily upsert.
    """
    if to_date is None:
        to_date = date.today().isoformat()

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/eod/{eodhd_symbol}",
                params=_params(**{"from": from_date, "to": to_date}),
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []

    rows = []
    for bar in data:
        if not bar.get("close"):
            continue
        rows.append(
            {
                "ticker": display_ticker,
                "date": bar["date"],
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar["close"],
                "volume": int(bar["volume"]) if bar.get("volume") else None,
                "asset_class": asset_class,
                "source": "eodhd",
                "adjusted_flag": True,
            }
        )
    return rows


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_fundamentals(eodhd_symbol: str) -> dict:
    """Fetch fundamental data for a symbol."""
    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/fundamentals/{eodhd_symbol}",
                params=_params(),
            )
            if resp.status_code == 200:
                return resp.json()
    return {}


async def fetch_all_eodhd_ohlcv(from_date: str = "1993-01-01", asset_classes: list = None) -> list[dict]:
    """Fetch EODHD instruments. Optionally filter by asset_class list.

    asset_classes: e.g. ["bond"] or ["fx","crypto"] — None = all
    """
    tasks = []
    for display_ticker, (eodhd_sym, asset_class) in ALL_EODHD.items():
        if asset_classes and asset_class not in asset_classes:
            continue
        tasks.append(get_eod(eodhd_sym, display_ticker, asset_class, from_date))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_rows = []
    for r in results:
        if isinstance(r, list):
            all_rows.extend(r)
        elif isinstance(r, Exception):
            print(f"  Error fetching EODHD data: {r}")
    return all_rows


async def fetch_bonds_ohlcv(from_date: str = "1980-01-01") -> list[dict]:
    """Fetch only bond yield data from GBOND exchange."""
    return await fetch_all_eodhd_ohlcv(from_date=from_date, asset_classes=["bond"])
