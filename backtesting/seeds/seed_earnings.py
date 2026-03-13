"""Seed earnings report dates via Marketdata.app.

Fetches historical earnings announcement dates for all equities in the
universe, storing them in the earnings_dates table. These dates are used
by the scanner to exclude forward returns around earnings announcements.

Usage:
    python -m backtesting.seeds.seed_earnings           # all tickers
    python -m backtesting.seeds.seed_earnings --limit 100  # first 100 tickers
    python -m backtesting.seeds.seed_earnings --dry-run  # show what would be seeded
"""
import asyncio
import sys

from backtesting.wrappers.marketdata import get_earnings_dates
from backtesting.db import get_client, get_equity_universe


async def seed_earnings(
    limit: int = 0,
    batch_size: int = 20,
    dry_run: bool = False,
):
    print("=== Seeding Earnings Dates via Marketdata.app ===")

    # Get universe
    tickers = get_equity_universe()
    if limit > 0:
        tickers = tickers[:limit]
    print(f"  Universe: {len(tickers)} tickers")

    if dry_run:
        print(f"\n  DRY RUN — would seed earnings for {len(tickers)} tickers")
        for i in range(0, min(len(tickers), 100), 20):
            print(f"    {', '.join(tickers[i:i+20])}")
        return

    # Fetch earnings in batches
    total_rows = 0
    errors = 0
    no_data = 0
    consecutive_empty = 0  # Track consecutive empty batches for IP block detection
    n_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(tickers), batch_size):
        batch = tickers[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        print(
            f"  Batch {batch_num}/{n_batches} ({batch[0]}..{batch[-1]})...",
            end=" ",
            flush=True,
        )

        tasks = [get_earnings_dates(sym) for sym in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows = []
        for i, r in enumerate(results):
            if isinstance(r, list) and r:
                for entry in r:
                    rows.append({
                        "ticker": entry["ticker"],
                        "date": entry["date"],
                    })
            elif isinstance(r, Exception):
                errors += 1
            else:
                no_data += 1

        if rows:
            consecutive_empty = 0
            # Upsert earnings dates
            client = get_client()
            for j in range(0, len(rows), 500):
                chunk = rows[j : j + 500]
                try:
                    client.table("earnings_dates").upsert(
                        chunk, on_conflict="ticker,date"
                    ).execute()
                except Exception as e:
                    print(f"\n    Upsert error: {e}")
                    errors += 1

            total_rows += len(rows)
            print(f"{len(rows)} dates ({total_rows} total)")
        else:
            consecutive_empty += 1
            print("no data")

            # If 10+ consecutive batches return no data, likely IP blocked
            if consecutive_empty >= 10:
                print(f"\n  WARNING: {consecutive_empty} consecutive empty batches.")
                print("  Likely Marketdata.app IP block. Pausing 5 minutes...")
                await asyncio.sleep(300)  # Wait 5 minutes
                consecutive_empty = 0  # Reset counter after pause

        # Respect rate limits
        await asyncio.sleep(1)

    print(f"\n=== Earnings seed complete ===")
    print(f"  Tickers attempted: {len(tickers)}")
    print(f"  Total dates upserted: {total_rows:,}")
    print(f"  No data: {no_data}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    limit = 0
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg.startswith("--limit"):
            if "=" in arg:
                limit = int(arg.split("=")[1])
        elif not arg.startswith("--"):
            try:
                limit = int(arg)
            except ValueError:
                pass

    asyncio.run(seed_earnings(limit=limit, dry_run=dry_run))
