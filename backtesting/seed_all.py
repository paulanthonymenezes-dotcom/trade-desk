"""Run all seed scripts in order.

Usage: python -m backtesting.seed_all [--equities] [--eodhd] [--events] [--financeflow] [--worldbank] [--all]
"""
import asyncio
import sys


async def main():
    args = set(sys.argv[1:])
    run_all = "--all" in args or not args

    if run_all or "--events" in args:
        from backtesting.seeds.seed_events import seed_events
        seed_events()

    if run_all or "--eodhd" in args:
        from backtesting.seeds.seed_eodhd import seed_eodhd
        await seed_eodhd()

    if run_all or "--financeflow" in args:
        from backtesting.seeds.seed_financeflow import seed_all_financeflow
        await seed_all_financeflow()

    if run_all or "--worldbank" in args:
        from backtesting.seeds.seed_worldbank import seed_worldbank
        await seed_worldbank()

    if run_all or "--equities" in args:
        from backtesting.seeds.seed_equities import seed_equities
        await seed_equities()

    print("\n=== All seeds complete ===")


if __name__ == "__main__":
    asyncio.run(main())
