"""Seed bond yields, economic calendar, and indicators via FinanceFlowAPI.

Bond yield candles go into ohlcv_daily (asset_class='bond').
Economic calendar and indicators go into their own tables.

Usage:
    python -m backtesting.seeds.seed_financeflow [from_date]
    python -m backtesting.seeds.seed_financeflow --bonds-only
    python -m backtesting.seeds.seed_financeflow --calendar-only
    python -m backtesting.seeds.seed_financeflow --indicators-only
"""
import asyncio
import sys

from backtesting.wrappers.financeflow import (
    BOND_UNIVERSE,
    get_bond_candles,
    fetch_economic_calendar_range,
    get_economic_indicators,
)
from backtesting.db import upsert_batch


# Countries to fetch economic indicators for
INDICATOR_COUNTRIES = [
    "United States", "United Kingdom", "Germany", "Japan",
    "Canada", "Australia", "China", "Brazil", "India",
    "South Korea", "Mexico", "Switzerland",
]


async def seed_bonds(from_date: str = "2000-01-01"):
    """Seed bond yield candles into ohlcv_daily."""
    print("=== Seeding Bond Yield Candles (FinanceFlowAPI) ===")

    total_instruments = sum(len(bonds) for bonds in BOND_UNIVERSE.values())
    print(f"  Instruments: {total_instruments}")
    print(f"  From: {from_date}")

    total_rows = 0
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
                if rows:
                    n = upsert_batch("ohlcv_daily", rows)
                    total_rows += n
                    print(f"    {n} rows upserted (first: {rows[0]['date']}, last: {rows[-1]['date']})")
                else:
                    print(f"    No data returned")
            except Exception as e:
                print(f"    Error: {e}")

            await asyncio.sleep(1)  # rate limit courtesy

    print(f"  Total bond rows upserted: {total_rows}")
    print("=== Bond seed complete ===\n")


async def seed_calendar():
    """Seed upcoming 60 days of economic calendar events."""
    print("=== Seeding Economic Calendar (FinanceFlowAPI) ===")

    try:
        events = await fetch_economic_calendar_range()
        if events:
            n = upsert_batch("economic_calendar", events)
            print(f"  {n} calendar events upserted")
        else:
            print("  No calendar events returned")
    except Exception as e:
        print(f"  Error: {e}")

    print("=== Calendar seed complete ===\n")


async def seed_indicators():
    """Seed economic indicators for major economies."""
    print("=== Seeding Economic Indicators (FinanceFlowAPI) ===")
    print(f"  Countries: {len(INDICATOR_COUNTRIES)}")

    total_rows = 0
    for country in INDICATOR_COUNTRIES:
        print(f"  Fetching indicators for {country}...")
        try:
            rows = await get_economic_indicators(country=country)
            if rows:
                n = upsert_batch("economic_indicators", rows)
                total_rows += n
                print(f"    {n} indicators upserted")
            else:
                print(f"    No data returned")
        except Exception as e:
            print(f"    Error: {e}")

        await asyncio.sleep(1)

    print(f"  Total indicator rows upserted: {total_rows}")
    print("=== Indicators seed complete ===\n")


async def seed_all_financeflow(from_date: str = "2000-01-01"):
    """Run all FinanceFlowAPI seeds."""
    await seed_bonds(from_date)
    await seed_calendar()
    await seed_indicators()


if __name__ == "__main__":
    from_date = "2000-01-01"
    bonds_only = False
    calendar_only = False
    indicators_only = False

    for arg in sys.argv[1:]:
        if arg == "--bonds-only":
            bonds_only = True
        elif arg == "--calendar-only":
            calendar_only = True
        elif arg == "--indicators-only":
            indicators_only = True
        elif not arg.startswith("--"):
            from_date = arg

    if bonds_only:
        asyncio.run(seed_bonds(from_date))
    elif calendar_only:
        asyncio.run(seed_calendar())
    elif indicators_only:
        asyncio.run(seed_indicators())
    else:
        asyncio.run(seed_all_financeflow(from_date))
