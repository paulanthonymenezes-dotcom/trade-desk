"""Daily OHLCV update — fetch only recent data for all seeded assets.

Fetches the last N days of OHLCV for all tickers in the equity universe
and all EODHD-managed assets (FX, crypto, indices, bonds, commodities).
Uses upsert to update existing rows and add new ones.

Usage:
    python -m backtesting.seeds.seed_daily_update           # last 5 days
    python -m backtesting.seeds.seed_daily_update 30        # last 30 days
    python -m backtesting.seeds.seed_daily_update --dry-run # preview
"""
import asyncio
import sys
from datetime import date, timedelta

from backtesting.db import upsert_batch, get_equity_universe
from backtesting.wrappers.eodhd import get_eod, fetch_all_eodhd_ohlcv


async def daily_update(lookback_days: int = 5, batch_size: int = 50, dry_run: bool = False):
    from_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    print(f"=== Daily OHLCV Update ===")
    print(f"  From: {from_date} ({lookback_days} day lookback)")

    # ── Part 1: EODHD managed assets (FX, crypto, indices, bonds, commodities) ──
    print("\n  Updating EODHD assets (FX, crypto, indices, bonds, commodities)...")
    if not dry_run:
        eodhd_rows = await fetch_all_eodhd_ohlcv(from_date=from_date)
        if eodhd_rows:
            n = upsert_batch("ohlcv_daily", eodhd_rows)
            print(f"    EODHD assets: {n} rows upserted")
        else:
            print("    EODHD assets: no new data")
    else:
        print("    [DRY RUN] Would update ~130 EODHD symbols")

    # ── Part 2: US Equities via EODHD ──
    tickers = get_equity_universe()
    print(f"\n  Updating {len(tickers)} US equities...")

    if dry_run:
        print(f"    [DRY RUN] Would update {len(tickers)} equity tickers")
        return

    total_rows = 0
    errors = 0
    n_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(tickers), batch_size):
        batch = tickers[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1

        if batch_num % 10 == 1 or batch_num == n_batches:
            print(f"    Batch {batch_num}/{n_batches}...", end=" ", flush=True)

        tasks = [
            get_eod(f"{sym}.US", sym, "equity", from_date)
            for sym in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        rows = []
        for i, r in enumerate(results):
            if isinstance(r, list):
                rows.extend(r)
            elif isinstance(r, Exception):
                errors += 1

        if rows:
            n = upsert_batch("ohlcv_daily", rows)
            total_rows += n

        if batch_num % 10 == 1 or batch_num == n_batches:
            print(f"{total_rows:,} rows total")

        await asyncio.sleep(0.3)

    print(f"\n=== Daily update complete ===")
    print(f"  Equity rows upserted: {total_rows:,}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    lookback = 5
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif not arg.startswith("--"):
            try:
                lookback = int(arg)
            except ValueError:
                pass

    asyncio.run(daily_update(lookback_days=lookback, dry_run=dry_run))
