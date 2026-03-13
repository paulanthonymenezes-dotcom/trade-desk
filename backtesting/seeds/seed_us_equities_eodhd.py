"""Seed full US equity universe via EODHD.

Fetches the exchange listing for US, filters to common stocks on major
exchanges (NYSE, NASDAQ, AMEX, etc.), then pulls daily OHLCV for each.

Usage:
    python -m backtesting.seeds.seed_us_equities_eodhd           # all from 2000
    python -m backtesting.seeds.seed_us_equities_eodhd 2020-01-01  # recent only
    python -m backtesting.seeds.seed_us_equities_eodhd --dry-run   # show what would be seeded
"""
import asyncio
import sys

import httpx

from backtesting.config import EODHD_API_TOKEN
from backtesting.wrappers.eodhd import get_eod
from backtesting.db import upsert_batch, save_equity_universe


MAJOR_EXCHANGES = {"NYSE", "NASDAQ", "NYSE MKT", "AMEX", "BATS", "NYSE ARCA", "NYSEARCA"}


def fetch_us_equity_universe() -> list[dict]:
    """Fetch all common stocks on major US exchanges from EODHD."""
    resp = httpx.get(
        "https://eodhd.com/api/exchange-symbol-list/US",
        params={"api_token": EODHD_API_TOKEN, "fmt": "json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    stocks = [
        d for d in data
        if d.get("Type") == "Common Stock"
        and d.get("Exchange") in MAJOR_EXCHANGES
        and "." not in d["Code"]
        and "-" not in d["Code"]
        and len(d["Code"]) <= 5
    ]
    return stocks


async def seed_us_equities(from_date: str = "2000-01-01", batch_size: int = 50, dry_run: bool = False):
    print("=== Seeding US Equities via EODHD ===")
    print(f"  From: {from_date}")

    # Step 1: Get universe
    print("  Fetching US exchange listing...")
    stocks = fetch_us_equity_universe()
    tickers = sorted(set(s["Code"] for s in stocks))
    print(f"  Universe: {len(tickers)} common stocks on major exchanges")

    # Save the universe cache for the scanner
    save_equity_universe(tickers)

    if dry_run:
        print(f"\n  DRY RUN — would seed {len(tickers)} tickers:")
        for i in range(0, len(tickers), 20):
            print(f"    {', '.join(tickers[i:i+20])}")
        return

    # Step 2: Fetch OHLCV in batches
    total_rows = 0
    errors = 0
    n_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(tickers), batch_size):
        batch = tickers[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        print(f"  Batch {batch_num}/{n_batches} ({batch[0]}..{batch[-1]})...", end=" ", flush=True)

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
            print(f"{n} rows ({total_rows} total)")
        else:
            print("no data")

        # Small pause between batches to be polite
        await asyncio.sleep(0.3)

    print(f"\n=== US Equities seed complete ===")
    print(f"  Tickers: {len(tickers)}")
    print(f"  Rows upserted: {total_rows:,}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    from_date = "2000-01-01"
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif not arg.startswith("--"):
            from_date = arg

    asyncio.run(seed_us_equities(from_date, dry_run=dry_run))
