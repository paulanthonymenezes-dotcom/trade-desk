"""Seed FX, crypto, global indices, commodities, and bonds via EODHD API.

Fetches 30+ years of daily history for all configured instruments.
Use --bonds-only to seed just government bond yields.
Use --no-bonds to skip bonds (seed everything else).
"""
import asyncio
import sys

from backtesting.wrappers.eodhd import ALL_EODHD, BONDS, get_eod
from backtesting.db import upsert_batch


async def seed_eodhd(from_date: str = "1993-01-01", asset_filter: str = None):
    if asset_filter == "bonds":
        instruments = {k: v for k, v in ALL_EODHD.items() if v[1] == "bond"}
        label = "bonds only"
        # Bonds go back further
        if from_date == "1993-01-01":
            from_date = "1980-01-01"
    elif asset_filter == "no-bonds":
        instruments = {k: v for k, v in ALL_EODHD.items() if v[1] != "bond"}
        label = "FX, crypto, indices, commodities (no bonds)"
    else:
        instruments = ALL_EODHD
        label = "all instruments"

    print(f"=== Seeding EODHD — {label} ===")
    print(f"  Instruments: {len(instruments)}")
    print(f"  From: {from_date}")

    total_rows = 0
    for display_ticker, (eodhd_sym, asset_class) in instruments.items():
        print(f"  Fetching {display_ticker} ({eodhd_sym}, {asset_class})...")
        try:
            rows = await get_eod(eodhd_sym, display_ticker, asset_class, from_date)
            if rows:
                n = upsert_batch("ohlcv_daily", rows)
                total_rows += n
                print(f"    {n} rows upserted (first: {rows[0]['date']}, last: {rows[-1]['date']})")
            else:
                print(f"    No data returned")
        except Exception as e:
            print(f"    Error: {e}")

        await asyncio.sleep(0.5)  # Rate limit courtesy

    print(f"  Total EODHD rows upserted: {total_rows}")
    print("=== EODHD seed complete ===\n")


if __name__ == "__main__":
    from_date = "1993-01-01"
    asset_filter = None

    for arg in sys.argv[1:]:
        if arg == "--bonds-only":
            asset_filter = "bonds"
        elif arg == "--no-bonds":
            asset_filter = "no-bonds"
        elif not arg.startswith("--"):
            from_date = arg

    asyncio.run(seed_eodhd(from_date, asset_filter))
