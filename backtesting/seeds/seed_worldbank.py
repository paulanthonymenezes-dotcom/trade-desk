"""Seed macro indicators from World Bank API.

Fetches 60+ years of GDP, CPI, unemployment, trade, etc. for 20 countries.
Free API — no key required.

Usage:
    python -m backtesting.seeds.seed_worldbank
    python -m backtesting.seeds.seed_worldbank --country US
    python -m backtesting.seeds.seed_worldbank --indicator FP.CPI.TOTL.ZG
"""
import asyncio
import sys

from backtesting.wrappers.worldbank import (
    COUNTRIES,
    INDICATORS,
    fetch_all_macro_data,
    fetch_country_indicators,
)
from backtesting.db import upsert_batch


async def seed_worldbank(
    country_codes=None,
    indicator_codes=None,
):
    """Seed macro time series from World Bank."""
    countries = country_codes or list(COUNTRIES.keys())
    indicators = indicator_codes or list(INDICATORS.keys())

    print("=== Seeding World Bank Macro Indicators ===")
    print(f"  Countries: {len(countries)}")
    print(f"  Indicators: {len(indicators)}")
    print(f"  Estimated API calls: {len(countries) * len(indicators)}")

    all_rows = await fetch_all_macro_data(countries, indicators)

    if all_rows:
        print(f"\n  Upserting {len(all_rows)} data points...")
        n = upsert_batch("macro_timeseries", all_rows)
        print(f"  {n} rows upserted")
    else:
        print("  No data returned")

    print("=== World Bank seed complete ===\n")


if __name__ == "__main__":
    country = None
    indicator = None

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--country" and i < len(sys.argv) - 1:
            country = [sys.argv[i + 1]]
        elif arg == "--indicator" and i < len(sys.argv) - 1:
            indicator = [sys.argv[i + 1]]

    asyncio.run(seed_worldbank(country, indicator))
