from __future__ import annotations

"""Seed expanded universe — S&P 500 + NASDAQ 100 + key ETFs.

Adds ~300 liquid US equities beyond the original 75-symbol universe.
"""
import asyncio
import sys

from backtesting.wrappers.marketdata import get_candles
from backtesting.db import upsert_batch, get_client, fetch_ohlcv

# ── S&P 500 top constituents + NASDAQ 100 + sector leaders ──────────────────
# Curated list of ~250 additional liquid US names
EXPANDED_UNIVERSE = [
    # ── Already seeded (74 symbols) — skip these ──
    # SPY, QQQ, IWM, DIA, VTI, XLF, XLK, XLE, XLV, XLI, XLP, XLU, XLB, XLRE, XLC,
    # TLT, HYG, LQD, GLD, SLV, USO, AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA,
    # JPM, BAC, GS, MS, C, WFC, UNH, JNJ, PFE, ABBV, MRK, LLY, XOM, CVX, COP, SLB,
    # WMT, COST, HD, MCD, NKE, SBUX, TGT, BA, CAT, GE, UPS, HON, CRM, ADBE, ORCL,
    # INTC, AMD, AVGO, QCOM, DIS, NFLX, V, MA, PYPL, SQ, COIN, SOFI, PLTR, RIVN, LCID

    # ── Additional NASDAQ 100 ──
    "ABNB", "ADP", "AEP", "AMAT", "AMGN", "ANSS", "APP",
    "ARM", "ASML", "AZN", "BIIB", "BKNG", "BKR",
    "CCEP", "CDNS", "CDW", "CEG", "CHTR",
    "CMCSA", "CRWD", "CSGP", "CTAS", "CTSH", "DASH", "DDOG",
    "DXCM", "EA", "EXC", "FANG", "FAST", "FTNT",
    "GEHC", "GILD", "GFS", "GRAB", "HON", "IDXX", "ILMN", "INTU",
    "ISRG", "KDP", "KHC", "KLAC", "LRCX",
    "LULU", "MAR", "MCHP", "MDLZ", "MELI", "MNST",
    "MRVL", "NXPI", "ODFL", "ON", "PANW", "PAYX",
    "PCAR", "PDD", "PEP", "PYPL", "REGN", "ROP", "ROST",
    "SBUX", "SMCI", "SNPS", "TEAM", "TMUS",
    "TTWO", "TXN", "VRSK", "VRTX", "WBD", "WDAY", "ZS",

    # ── Additional S&P 500 — Industrials ──
    "MMM", "ABT", "ACN", "AIG", "ALL", "AMT", "AXP",
    "BDX", "BLK", "BMY", "BSX", "CB", "CI", "CL", "CME", "COF",
    "CCI", "CSX", "CVS", "D", "DE", "DHR", "DOW",
    "DUK", "ECL", "EL", "EMR", "EOG", "ETN", "EW",
    "F", "FCX", "FDX", "FIS", "FISV", "GD", "GM",
    "HCA", "HLT", "HSY", "HUM", "ICE", "ITW",
    "JCI", "KMB", "KO", "LHX", "LIN", "LMT", "LOW",
    "MCK", "MDT", "MET", "MMC", "MO", "MPC",
    "MU", "NEE", "NOC", "NOW", "NSC",
    "OXY", "PFG", "PG", "PGR", "PH", "PNC", "PPG",
    "PRU", "PSA", "PSX", "PXD", "RACE", "RCL",
    "REGN", "RTX", "SCHW", "SHW",
    "SLB", "SO", "SPG", "SRE", "STZ", "SYK", "SYY",
    "T", "TDG", "TFC", "TMO", "TRGP", "TRV",
    "UBER", "UNP", "URI", "USB", "VLO", "VMC", "VZ",
    "WAB", "WEC", "WELL", "WM", "WMB", "XEL", "ZBH", "ZTS",

    # ── Additional S&P 500 — Tech / Growth ──
    "SNOW", "NET", "DKNG", "RBLX", "HOOD", "PATH", "U",
    "CFLT", "MDB", "HUBS", "BILL", "TTD", "DUOL",
    "PINS", "SNAP", "ROKU", "SHOP", "SE", "MSTR",

    # ── Popular retail / meme / high-volume ──
    "GME", "AMC", "BBBY", "SPCE", "OPEN", "WISH",
    "NIO", "XPEV", "LI", "BABA", "JD", "PDD",
    "TSM", "SONY", "TM",

    # ── Additional ETFs ──
    "ARKK", "XBI", "KWEB", "EEM", "EFA", "FXI", "IBIT",
    "SMH", "SOXX", "XHB", "XRT", "KRE", "OIH",
    "TQQQ", "SQQQ", "UVXY", "VXX",
]

# De-duplicate and remove any that are already in the DB
def _get_existing_tickers() -> set:
    """Get tickers already in the database."""
    client = get_client()
    existing = set()
    offset = 0
    page = 1000
    while True:
        result = client.table("ohlcv_daily").select("ticker").range(offset, offset + page - 1).execute()
        for r in result.data:
            existing.add(r["ticker"])
        if len(result.data) < page:
            break
        offset += page
    return existing


async def seed_expanded(from_date: str = "2003-01-01", batch_concurrency: int = 10):
    existing = _get_existing_tickers()
    new_symbols = sorted(set(EXPANDED_UNIVERSE) - existing)
    print(f"=== Seeding {len(new_symbols)} NEW symbols (skipping {len(existing)} already in DB) ===")

    if not new_symbols:
        print("Nothing new to seed!")
        return

    total_rows = 0
    errors = []

    for batch_start in range(0, len(new_symbols), batch_concurrency):
        batch = new_symbols[batch_start: batch_start + batch_concurrency]
        batch_num = batch_start // batch_concurrency + 1
        total_batches = (len(new_symbols) + batch_concurrency - 1) // batch_concurrency
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

        # Respect rate limits
        await asyncio.sleep(6)

    print(f"\n=== Expanded seed complete ===")
    print(f"  New OHLCV rows: {total_rows}")
    print(f"  New symbols attempted: {len(new_symbols)}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors[:20]:
            print(f"    - {e}")


if __name__ == "__main__":
    from_date = sys.argv[1] if len(sys.argv) > 1 else "2003-01-01"
    asyncio.run(seed_expanded(from_date))
