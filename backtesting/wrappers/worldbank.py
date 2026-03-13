from __future__ import annotations

"""World Bank API wrapper — global macro indicators (GDP, CPI, unemployment, etc.).

Free API, no key required. Data goes back to 1960 for most countries.
Docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
"""
import asyncio
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://api.worldbank.org/v2"
_semaphore = asyncio.Semaphore(5)  # be respectful to free API


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


# ── Indicator registry ────────────────────────────────────────────────────────
# code -> (display_name, category, units)

INDICATORS = {
    # Economy
    "FP.CPI.TOTL.ZG": ("Inflation Rate (CPI)", "Economy", "%"),
    "NY.GDP.MKTP.CD": ("GDP (Current USD)", "Economy", "USD"),
    "NY.GDP.MKTP.KD.ZG": ("GDP Growth Rate", "Economy", "%"),
    "NY.GDP.PCAP.CD": ("GDP Per Capita", "Economy", "USD"),
    "GC.DOD.TOTL.GD.ZS": ("Debt to GDP", "Economy", "%"),
    "NV.IND.MANF.ZS": ("Manufacturing (% GDP)", "Economy", "%"),
    "NY.GNP.MKTP.CD": ("GNI (Gross National Income)", "Economy", "USD"),
    "NY.GNP.PCAP.CD": ("GNI Per Capita", "Economy", "USD"),

    # Trade
    "NE.TRD.GNFS.ZS": ("Trade (% GDP)", "Trade", "%"),
    "NE.EXP.GNFS.ZS": ("Exports (% GDP)", "Trade", "%"),
    "NE.IMP.GNFS.ZS": ("Imports (% GDP)", "Trade", "%"),
    "BN.CAB.XOKA.GD.ZS": ("Current Account (% GDP)", "Trade", "%"),

    # Labor
    "SL.UEM.TOTL.ZS": ("Unemployment Rate", "Labor Force", "%"),
    "SL.TLF.TOTL.IN": ("Labor Force Total", "Labor Force", "people"),
    "SL.TLF.CACT.ZS": ("Labor Force Participation", "Labor Force", "%"),

    # Population
    "SP.POP.TOTL": ("Population", "Population", "people"),
    "SP.POP.GROW": ("Population Growth", "Population", "%"),
    "SP.DYN.LE00.IN": ("Life Expectancy", "Health", "years"),

    # Financial
    "FR.INR.RINR": ("Real Interest Rate", "Economy", "%"),
    "FI.RES.TOTL.CD": ("Total Reserves (incl Gold)", "Economy", "USD"),
    "PA.NUS.FCRF": ("Exchange Rate (per USD)", "Economy", "LCU"),
}

# Countries to seed
COUNTRIES = {
    "US": "United States",
    "GB": "United Kingdom",
    "DE": "Germany",
    "JP": "Japan",
    "CN": "China",
    "BR": "Brazil",
    "IN": "India",
    "CA": "Canada",
    "AU": "Australia",
    "KR": "South Korea",
    "MX": "Mexico",
    "CH": "Switzerland",
    "FR": "France",
    "IT": "Italy",
    "RU": "Russia",
    "ZA": "South Africa",
    "TR": "Turkey",
    "SA": "Saudi Arabia",
    "ID": "Indonesia",
    "IR": "Iran",
}


# ── API functions ─────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def get_indicator(
    country_code: str,
    indicator_code: str,
    start_year: int = 1960,
    end_year: int = 2026,
) -> list[dict]:
    """Fetch annual time series for one indicator / one country.

    Returns list of dicts ready for macro_timeseries upsert.
    """
    indicator_info = INDICATORS.get(indicator_code, (indicator_code, "Other", ""))
    display_name, category, units = indicator_info
    country_name = COUNTRIES.get(country_code, country_code)

    async with _semaphore:
        async with _client() as c:
            resp = await c.get(
                f"{BASE}/country/{country_code}/indicator/{indicator_code}",
                params={
                    "format": "json",
                    "per_page": 200,
                    "date": f"{start_year}:{end_year}",
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()

    # Response: [metadata, records]
    if not isinstance(data, list) or len(data) < 2:
        return []

    records = data[1]
    if not records:
        return []

    rows = []
    for r in records:
        if r.get("value") is None:
            continue
        rows.append(
            {
                "country_code": country_code,
                "country_name": r.get("country", {}).get("value", country_name),
                "indicator_code": indicator_code,
                "indicator_name": display_name,
                "category": category,
                "units": units,
                "year": int(r["date"]),
                "value": float(r["value"]),
                "source": "worldbank",
            }
        )
    return rows


async def fetch_country_indicators(
    country_code: str,
    indicator_codes: list = None,
) -> list[dict]:
    """Fetch all indicators for a single country."""
    if indicator_codes is None:
        indicator_codes = list(INDICATORS.keys())

    all_rows = []
    for code in indicator_codes:
        rows = await get_indicator(country_code, code)
        all_rows.extend(rows)
        await asyncio.sleep(0.3)  # rate limit courtesy

    return all_rows


async def fetch_all_macro_data(
    country_codes: list = None,
    indicator_codes: list = None,
) -> list[dict]:
    """Fetch all indicators for all countries.

    ~20 countries × ~22 indicators = ~440 API calls.
    With rate limiting, takes about 3-4 minutes.
    """
    if country_codes is None:
        country_codes = list(COUNTRIES.keys())
    if indicator_codes is None:
        indicator_codes = list(INDICATORS.keys())

    all_rows = []
    for cc in country_codes:
        print(f"  Fetching {COUNTRIES.get(cc, cc)}...")
        rows = await fetch_country_indicators(cc, indicator_codes)
        all_rows.extend(rows)
        if rows:
            print(f"    {len(rows)} data points")
        else:
            print(f"    No data")

    return all_rows
