from __future__ import annotations

"""Seed OHLCV data using a curated universe + Marketdata.app candles endpoint.

Bypasses the screener (which requires a paid plan) and uses a known liquid universe.
"""
import asyncio
import sys

from backtesting.wrappers.marketdata import get_candles, get_earnings_dates
from backtesting.db import upsert_batch, get_client

# Curated US universe — major indices, sector ETFs, and liquid equities
UNIVERSE = [
    # Major indices / ETFs
    "SPY", "QQQ", "IWM", "DIA", "VTI",
    # Sector ETFs
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Bonds/Rates/Commodities
    "TLT", "HYG", "LQD", "GLD", "SLV", "USO",
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    # Financials
    "JPM", "BAC", "GS", "MS", "C", "WFC",
    # Healthcare
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT",
    # Industrials
    "BA", "CAT", "GE", "UPS", "HON",
    # Tech/Software
    "CRM", "ADBE", "ORCL", "INTC", "AMD", "AVGO", "QCOM",
    # Other notables
    "DIS", "NFLX", "V", "MA", "PYPL", "SQ",
    "COIN", "SOFI", "PLTR", "RIVN", "LCID",
]


async def seed_direct(from_date: str = "2000-01-01", batch_concurrency: int = 10):
    print(f"=== Seeding {len(UNIVERSE)} symbols via Marketdata.app candles ===")

    total_rows = 0
    errors = []

    for batch_start in range(0, len(UNIVERSE), batch_concurrency):
        batch = UNIVERSE[batch_start: batch_start + batch_concurrency]
        batch_num = batch_start // batch_concurrency + 1
        total_batches = (len(UNIVERSE) + batch_concurrency - 1) // batch_concurrency
        print(f"  Batch {batch_num}/{total_batches}: {', '.join(batch)}")

        tasks = [get_candles(sym, from_date=from_date) for sym in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows = []
        for i, r in enumerate(results):
            if isinstance(r, list) and len(r) > 0:
                rows.extend(r)
                print(f"    {batch[i]}: {len(r)} bars")
            elif isinstance(r, Exception):
                errors.append(f"{batch[i]}: {r}")
                print(f"    {batch[i]}: ERROR - {r}")
            else:
                print(f"    {batch[i]}: no data")

        if rows:
            n = upsert_batch("ohlcv_daily", rows)
            total_rows += n
            print(f"    >> Upserted {n} rows (total: {total_rows})")

        # Respect rate limits — 100 req/min
        await asyncio.sleep(6)

    # Fetch earnings dates
    print("\nFetching earnings dates...")
    total_earnings = 0
    for batch_start in range(0, len(UNIVERSE), batch_concurrency):
        batch = UNIVERSE[batch_start: batch_start + batch_concurrency]
        tasks = [get_earnings_dates(sym) for sym in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        earnings_rows = []
        for i, r in enumerate(results):
            if isinstance(r, list) and len(r) > 0:
                for d in r:
                    earnings_rows.append({"ticker": batch[i], "date": d})

        if earnings_rows:
            client = get_client()
            for j in range(0, len(earnings_rows), 500):
                chunk = earnings_rows[j: j + 500]
                try:
                    client.table("earnings_dates").upsert(
                        chunk, on_conflict="ticker,date"
                    ).execute()
                except Exception as e:
                    print(f"    Earnings upsert error: {e}")
            total_earnings += len(earnings_rows)

        await asyncio.sleep(3)

    print(f"\n=== Seed complete ===")
    print(f"  OHLCV rows: {total_rows}")
    print(f"  Earnings dates: {total_earnings}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")


if __name__ == "__main__":
    from_date = sys.argv[1] if len(sys.argv) > 1 else "2003-01-01"
    asyncio.run(seed_direct(from_date))
