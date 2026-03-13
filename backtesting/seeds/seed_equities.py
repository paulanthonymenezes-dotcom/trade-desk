"""Seed US equities OHLCV via Marketdata.app.

DEPRECATED: Use seed_us_equities_eodhd.py instead.
The Marketdata.app screener endpoint returns 404 on the Trader plan.
EODHD provides the full US equity universe via exchange listing.

Original purpose: screens for stocks with market cap >$500M and avg daily
volume >500k, then fetches daily OHLCV back to 2000 for each symbol.
"""
import asyncio
import sys

from backtesting.wrappers.marketdata import get_candles, get_earnings_dates, screen_us_equities
from backtesting.db import upsert_batch, get_client


async def seed_equities(from_date: str = "2000-01-01", batch_concurrency: int = 20):
    print("=== Seeding US Equities via Marketdata.app ===")

    # Step 1: Screen universe
    print("Screening US equities (market cap >$500M, avg volume >500k)...")
    symbols = await screen_us_equities()
    print(f"  Found {len(symbols)} symbols")

    if not symbols:
        print("  No symbols found. Check API token and screener endpoint.")
        return

    # Step 2: Fetch OHLCV in batches
    total_rows = 0
    for batch_start in range(0, len(symbols), batch_concurrency):
        batch = symbols[batch_start : batch_start + batch_concurrency]
        print(f"  Fetching candles: batch {batch_start // batch_concurrency + 1} "
              f"({batch_start + 1}-{batch_start + len(batch)} of {len(symbols)})")

        tasks = [get_candles(sym, from_date=from_date) for sym in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows = []
        for i, r in enumerate(results):
            if isinstance(r, list):
                rows.extend(r)
            else:
                print(f"    Error fetching {batch[i]}: {r}")

        if rows:
            n = upsert_batch("ohlcv_daily", rows)
            total_rows += n
            print(f"    Upserted {n} rows (running total: {total_rows})")

        # Respect rate limits
        await asyncio.sleep(1)

    print(f"  Total OHLCV rows upserted: {total_rows}")

    # Step 3: Fetch earnings dates
    print("Fetching earnings dates...")
    total_earnings = 0
    for batch_start in range(0, len(symbols), batch_concurrency):
        batch = symbols[batch_start : batch_start + batch_concurrency]
        tasks = [get_earnings_dates(sym) for sym in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        earnings_rows = []
        for i, r in enumerate(results):
            if isinstance(r, list):
                for d in r:
                    earnings_rows.append({"ticker": batch[i], "date": d})

        if earnings_rows:
            client = get_client()
            for j in range(0, len(earnings_rows), 500):
                chunk = earnings_rows[j : j + 500]
                try:
                    client.table("earnings_dates").upsert(
                        chunk, on_conflict="ticker,date"
                    ).execute()
                except Exception as e:
                    print(f"    Earnings upsert error: {e}")
            total_earnings += len(earnings_rows)

        await asyncio.sleep(1)

    print(f"  Total earnings dates: {total_earnings}")
    print("=== US Equities seed complete ===\n")


if __name__ == "__main__":
    from_date = sys.argv[1] if len(sys.argv) > 1 else "2000-01-01"
    asyncio.run(seed_equities(from_date))
