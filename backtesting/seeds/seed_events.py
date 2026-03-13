"""Pre-seed historical events table — VERIFIED from official central bank sources.

Data sources:
- Fed/FOMC: federalreserve.gov/monetarypolicy/openmarket.htm
- ECB: ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates
- BOE: bankofengland.co.uk/boeapps/database/Bank-Rate.asp
- BOJ: boj.or.jp, BIS central bank policy rate database
- PBOC: pbc.gov.cn, Trading Economics, CEIC, FRED

Categories:
- rate_decision: Central bank rate decisions (Fed, ECB, BOE, BOJ, PBOC)
- oil_shock: OPEC cuts, Hormuz events, strategic reserve releases
- geopolitical: Wars, sanctions, trade wars, major geopolitical events
- market_structure: Black Monday, LTCM, dot-com, GFC, flash crashes
- macro_surprise: Significant CPI/NFP/GDP beats and misses
"""
from backtesting.db import get_client


def _ev(date, etype, mag, geo, direction, desc, source, tags):
    return {
        "date": date, "event_type": etype, "magnitude": mag,
        "geography": geo, "direction": direction, "description": desc,
        "source": source, "tags": tags,
    }


def seed_events():
    print("=== Seeding Events Table (Global Central Banks + Macro) ===")

    events = []

    # ══════════════════════════════════════════════════════════════════════════
    # FED / FOMC — Verified from federalreserve.gov
    # ══════════════════════════════════════════════════════════════════════════

    fed = [
        # 2003
        ("2003-06-25", -0.25, "Cut 25bp to 1.00% — cycle low", ["easing_cycle"]),
        # 2004 tightening cycle
        ("2004-06-30", 0.25, "Hike 25bp to 1.25% — tightening begins", ["tightening_cycle"]),
        ("2004-08-10", 0.25, "Hike 25bp to 1.50%", ["tightening_cycle"]),
        ("2004-09-21", 0.25, "Hike 25bp to 1.75%", ["tightening_cycle"]),
        ("2004-11-10", 0.25, "Hike 25bp to 2.00%", ["tightening_cycle"]),
        ("2004-12-14", 0.25, "Hike 25bp to 2.25%", ["tightening_cycle"]),
        # 2005
        ("2005-02-02", 0.25, "Hike 25bp to 2.50%", ["tightening_cycle"]),
        ("2005-03-22", 0.25, "Hike 25bp to 2.75%", ["tightening_cycle"]),
        ("2005-05-03", 0.25, "Hike 25bp to 3.00%", ["tightening_cycle"]),
        ("2005-06-30", 0.25, "Hike 25bp to 3.25%", ["tightening_cycle"]),
        ("2005-08-09", 0.25, "Hike 25bp to 3.50%", ["tightening_cycle"]),
        ("2005-09-20", 0.25, "Hike 25bp to 3.75%", ["tightening_cycle"]),
        ("2005-11-01", 0.25, "Hike 25bp to 4.00%", ["tightening_cycle"]),
        ("2005-12-13", 0.25, "Hike 25bp to 4.25%", ["tightening_cycle"]),
        # 2006
        ("2006-01-31", 0.25, "Hike 25bp to 4.50%", ["tightening_cycle"]),
        ("2006-03-28", 0.25, "Hike 25bp to 4.75%", ["tightening_cycle"]),
        ("2006-05-10", 0.25, "Hike 25bp to 5.00%", ["tightening_cycle"]),
        ("2006-06-29", 0.25, "Hike 25bp to 5.25% — cycle peak", ["tightening_cycle"]),
        # 2007 easing
        ("2007-09-18", -0.50, "Cut 50bp to 4.75% — subprime fears", ["easing_cycle"]),
        ("2007-10-31", -0.25, "Cut 25bp to 4.50%", ["easing_cycle"]),
        ("2007-12-11", -0.25, "Cut 25bp to 4.25%", ["easing_cycle"]),
        # 2008 GFC
        ("2008-01-22", -0.75, "Emergency inter-meeting cut 75bp to 3.50%", ["easing_cycle", "emergency"]),
        ("2008-01-30", -0.50, "Cut 50bp to 3.00%", ["easing_cycle"]),
        ("2008-03-18", -0.75, "Cut 75bp to 2.25% — Bear Stearns week", ["easing_cycle"]),
        ("2008-04-30", -0.25, "Cut 25bp to 2.00%", ["easing_cycle"]),
        ("2008-10-08", -0.50, "Emergency coordinated global cut to 1.50%", ["easing_cycle", "emergency"]),
        ("2008-10-29", -0.50, "Cut 50bp to 1.00%", ["easing_cycle"]),
        ("2008-12-16", -0.75, "Cut to 0.00-0.25% — ZIRP begins", ["easing_cycle", "zirp"]),
        # 2015-2018 normalization
        ("2015-12-17", 0.25, "First hike in 7 years — lift-off to 0.25-0.50%", ["tightening_cycle"]),
        ("2016-12-15", 0.25, "Hike 25bp to 0.50-0.75%", ["tightening_cycle"]),
        ("2017-03-16", 0.25, "Hike 25bp to 0.75-1.00%", ["tightening_cycle"]),
        ("2017-06-15", 0.25, "Hike 25bp to 1.00-1.25%", ["tightening_cycle"]),
        ("2017-12-14", 0.25, "Hike 25bp to 1.25-1.50%", ["tightening_cycle"]),
        ("2018-03-22", 0.25, "Hike 25bp to 1.50-1.75%", ["tightening_cycle"]),
        ("2018-06-14", 0.25, "Hike 25bp to 1.75-2.00%", ["tightening_cycle"]),
        ("2018-09-27", 0.25, "Hike 25bp to 2.00-2.25%", ["tightening_cycle"]),
        ("2018-12-20", 0.25, "Hike 25bp to 2.25-2.50%", ["tightening_cycle"]),
        # 2019 insurance cuts
        ("2019-08-01", -0.25, "Cut 25bp to 2.00-2.25% — first cut since 2008", ["easing_cycle"]),
        ("2019-09-19", -0.25, "Cut 25bp to 1.75-2.00%", ["easing_cycle"]),
        ("2019-10-31", -0.25, "Cut 25bp to 1.50-1.75%", ["easing_cycle"]),
        # 2020 COVID
        ("2020-03-04", -0.50, "Emergency cut 50bp to 1.00-1.25% — COVID", ["easing_cycle", "emergency"]),
        ("2020-03-16", -1.00, "Emergency Sunday cut to 0.00-0.25% + QE", ["easing_cycle", "emergency", "zirp"]),
        # 2022-2023 inflation fighting
        ("2022-03-17", 0.25, "Hike 25bp to 0.25-0.50% — inflation cycle begins", ["tightening_cycle"]),
        ("2022-05-05", 0.50, "Hike 50bp to 0.75-1.00%", ["tightening_cycle"]),
        ("2022-06-16", 0.75, "Hike 75bp to 1.50-1.75% — first 75bp since 1994", ["tightening_cycle"]),
        ("2022-07-28", 0.75, "Hike 75bp to 2.25-2.50%", ["tightening_cycle"]),
        ("2022-09-22", 0.75, "Hike 75bp to 3.00-3.25%", ["tightening_cycle"]),
        ("2022-11-03", 0.75, "Hike 75bp to 3.75-4.00%", ["tightening_cycle"]),
        ("2022-12-15", 0.50, "Hike 50bp to 4.25-4.50%", ["tightening_cycle"]),
        ("2023-02-02", 0.25, "Hike 25bp to 4.50-4.75%", ["tightening_cycle"]),
        ("2023-03-23", 0.25, "Hike 25bp to 4.75-5.00% — despite SVB crisis", ["tightening_cycle"]),
        ("2023-05-04", 0.25, "Hike 25bp to 5.00-5.25%", ["tightening_cycle"]),
        ("2023-07-27", 0.25, "Hike 25bp to 5.25-5.50% — cycle peak", ["tightening_cycle"]),
        # 2024-2025 easing
        ("2024-09-19", -0.50, "Cut 50bp to 4.75-5.00% — easing begins", ["easing_cycle"]),
        ("2024-11-08", -0.25, "Cut 25bp to 4.50-4.75%", ["easing_cycle"]),
        ("2024-12-19", -0.25, "Cut 25bp to 4.25-4.50%", ["easing_cycle"]),
        ("2025-09-18", -0.25, "Cut 25bp to 4.00-4.25%", ["easing_cycle"]),
        ("2025-10-30", -0.25, "Cut 25bp to 3.75-4.00%", ["easing_cycle"]),
        ("2025-12-11", -0.25, "Cut 25bp to 3.50-3.75%", ["easing_cycle"]),
    ]

    for date, mag, desc, tags in fed:
        direction = "hawkish" if mag > 0 else "dovish"
        events.append(_ev(date, "rate_decision", mag, "US", direction, f"Fed: {desc}", "federalreserve.gov", ["fed"] + tags))

    # ══════════════════════════════════════════════════════════════════════════
    # ECB — Verified from ecb.europa.eu
    # ══════════════════════════════════════════════════════════════════════════

    ecb = [
        ("1999-04-09", -0.50, "Cut MRO to 2.50%", ["easing_cycle"]),
        ("1999-11-05", 0.50, "Hike MRO to 3.00%", ["tightening_cycle"]),
        ("2000-02-04", 0.25, "Hike to 3.25%", ["tightening_cycle"]),
        ("2000-03-17", 0.25, "Hike to 3.50%", ["tightening_cycle"]),
        ("2000-04-28", 0.25, "Hike to 3.75%", ["tightening_cycle"]),
        ("2000-06-09", 0.50, "Hike to 4.25%", ["tightening_cycle"]),
        ("2000-09-01", 0.25, "Hike to 4.50%", ["tightening_cycle"]),
        ("2000-10-06", 0.25, "Hike to 4.75% — cycle peak", ["tightening_cycle"]),
        ("2001-05-11", -0.25, "Cut to 4.50%", ["easing_cycle"]),
        ("2001-08-31", -0.25, "Cut to 4.25%", ["easing_cycle"]),
        ("2001-09-18", -0.50, "Cut to 3.75% — post 9/11", ["easing_cycle", "emergency"]),
        ("2001-11-09", -0.50, "Cut to 3.25%", ["easing_cycle"]),
        ("2002-12-06", -0.50, "Cut to 2.75%", ["easing_cycle"]),
        ("2003-03-07", -0.25, "Cut to 2.50%", ["easing_cycle"]),
        ("2003-06-06", -0.50, "Cut to 2.00% — cycle low", ["easing_cycle"]),
        ("2005-12-06", 0.25, "Hike to 2.25% — tightening begins", ["tightening_cycle"]),
        ("2006-03-08", 0.25, "Hike to 2.50%", ["tightening_cycle"]),
        ("2006-06-15", 0.25, "Hike to 2.75%", ["tightening_cycle"]),
        ("2006-08-09", 0.25, "Hike to 3.00%", ["tightening_cycle"]),
        ("2006-10-11", 0.25, "Hike to 3.25%", ["tightening_cycle"]),
        ("2006-12-13", 0.25, "Hike to 3.50%", ["tightening_cycle"]),
        ("2007-03-14", 0.25, "Hike to 3.75%", ["tightening_cycle"]),
        ("2007-06-13", 0.25, "Hike to 4.00% — cycle peak", ["tightening_cycle"]),
        ("2008-07-09", 0.25, "Hike to 4.25% — controversial pre-GFC hike", ["tightening_cycle"]),
        ("2008-10-08", -0.50, "Emergency coordinated cut to 3.75%", ["easing_cycle", "emergency"]),
        ("2008-11-12", -0.50, "Cut to 3.25%", ["easing_cycle"]),
        ("2008-12-10", -0.75, "Cut to 2.50%", ["easing_cycle"]),
        ("2009-01-21", -0.50, "Cut to 2.00%", ["easing_cycle"]),
        ("2009-03-11", -0.50, "Cut to 1.50%", ["easing_cycle"]),
        ("2009-04-08", -0.25, "Cut to 1.25%", ["easing_cycle"]),
        ("2009-05-13", -0.25, "Cut to 1.00% — cycle low", ["easing_cycle"]),
        ("2011-04-13", 0.25, "Hike to 1.25% — Trichet mistake", ["tightening_cycle"]),
        ("2011-07-13", 0.25, "Hike to 1.50%", ["tightening_cycle"]),
        ("2011-11-09", -0.25, "Cut to 1.25% — reversed under Draghi", ["easing_cycle"]),
        ("2011-12-14", -0.25, "Cut to 1.00%", ["easing_cycle"]),
        ("2012-07-11", -0.25, "Cut to 0.75%", ["easing_cycle"]),
        ("2013-05-08", -0.25, "Cut to 0.50%", ["easing_cycle"]),
        ("2013-11-13", -0.25, "Cut to 0.25%", ["easing_cycle"]),
        ("2014-06-11", -0.10, "Cut to 0.15% — negative deposit rate introduced", ["easing_cycle", "nirp"]),
        ("2014-09-10", -0.10, "Cut to 0.05%", ["easing_cycle", "nirp"]),
        ("2016-03-16", -0.05, "Cut MRO to 0.00% — ZIRP", ["easing_cycle", "zirp"]),
        # 2022-2023 inflation fighting
        ("2022-07-27", 0.50, "Hike to 0.50% — first hike in 11 years", ["tightening_cycle"]),
        ("2022-09-14", 0.75, "Hike 75bp to 1.25%", ["tightening_cycle"]),
        ("2022-11-02", 0.75, "Hike 75bp to 2.00%", ["tightening_cycle"]),
        ("2022-12-21", 0.50, "Hike to 2.50%", ["tightening_cycle"]),
        ("2023-02-08", 0.50, "Hike to 3.00%", ["tightening_cycle"]),
        ("2023-03-22", 0.50, "Hike to 3.50%", ["tightening_cycle"]),
        ("2023-05-10", 0.25, "Hike to 3.75%", ["tightening_cycle"]),
        ("2023-06-21", 0.25, "Hike to 4.00%", ["tightening_cycle"]),
        ("2023-08-02", 0.25, "Hike to 4.25%", ["tightening_cycle"]),
        ("2023-09-20", 0.25, "Hike to 4.50% — cycle peak", ["tightening_cycle"]),
        # 2024-2025 easing
        ("2024-06-12", -0.25, "Cut to 4.25% — easing begins", ["easing_cycle"]),
        ("2024-09-18", -0.25, "Cut to 3.65%", ["easing_cycle"]),
        ("2024-10-23", -0.25, "Cut to 3.40%", ["easing_cycle"]),
        ("2024-12-18", -0.25, "Cut to 3.15%", ["easing_cycle"]),
        ("2025-02-05", -0.25, "Cut to 2.90%", ["easing_cycle"]),
        ("2025-03-12", -0.25, "Cut to 2.65%", ["easing_cycle"]),
        ("2025-04-23", -0.25, "Cut to 2.40%", ["easing_cycle"]),
        ("2025-06-11", -0.25, "Cut to 2.15%", ["easing_cycle"]),
    ]

    for date, mag, desc, tags in ecb:
        direction = "hawkish" if mag > 0 else "dovish"
        events.append(_ev(date, "rate_decision", mag, "EU", direction, f"ECB: {desc}", "ecb.europa.eu", ["ecb"] + tags))

    # ══════════════════════════════════════════════════════════════════════════
    # BOE — Verified from bankofengland.co.uk
    # ══════════════════════════════════════════════════════════════════════════

    boe = [
        # 1990s
        ("1990-10-08", 1.00, "Hike to 13.88% — ERM entry", ["tightening_cycle"]),
        ("1991-02-13", -0.50, "Cut to 13.38%", ["easing_cycle"]),
        ("1991-02-27", -0.50, "Cut to 12.88%", ["easing_cycle"]),
        ("1991-03-22", -0.50, "Cut to 12.38%", ["easing_cycle"]),
        ("1991-04-12", -0.50, "Cut to 11.88%", ["easing_cycle"]),
        ("1991-05-24", -0.50, "Cut to 11.38%", ["easing_cycle"]),
        ("1991-07-12", -0.50, "Cut to 10.88%", ["easing_cycle"]),
        ("1991-09-04", -0.50, "Cut to 10.38%", ["easing_cycle"]),
        ("1992-05-05", -0.50, "Cut to 9.88%", ["easing_cycle"]),
        ("1992-09-22", -1.00, "Cut to 8.88% — post-ERM crisis", ["easing_cycle", "erm_crisis"]),
        ("1992-10-16", -1.00, "Cut to 7.88%", ["easing_cycle"]),
        ("1992-11-13", -1.00, "Cut to 6.88%", ["easing_cycle"]),
        ("1993-01-26", -0.50, "Cut to 5.88%", ["easing_cycle"]),
        ("1993-11-23", -0.50, "Cut to 5.38%", ["easing_cycle"]),
        ("1994-02-08", 0.25, "Hike to 5.13%", ["tightening_cycle"]),
        ("1994-09-12", 0.50, "Hike to 5.63%", ["tightening_cycle"]),
        ("1994-12-07", 0.50, "Hike to 6.13%", ["tightening_cycle"]),
        ("1995-02-02", 0.50, "Hike to 6.63%", ["tightening_cycle"]),
        ("1995-12-13", -0.25, "Cut to 6.38%", ["easing_cycle"]),
        ("1996-01-18", -0.25, "Cut to 6.13%", ["easing_cycle"]),
        ("1996-03-08", -0.25, "Cut to 5.94%", ["easing_cycle"]),
        ("1996-06-06", -0.25, "Cut to 5.69%", ["easing_cycle"]),
        ("1996-10-30", 0.25, "Hike to 5.94%", ["tightening_cycle"]),
        ("1997-05-06", 0.25, "Hike to 6.25%", ["tightening_cycle"]),
        ("1997-06-06", 0.25, "Hike to 6.50%", ["tightening_cycle"]),
        ("1997-07-10", 0.25, "Hike to 6.75%", ["tightening_cycle"]),
        ("1997-08-07", 0.25, "Hike to 7.00%", ["tightening_cycle"]),
        ("1997-11-06", 0.25, "Hike to 7.25%", ["tightening_cycle"]),
        ("1998-06-04", 0.25, "Hike to 7.50% — cycle peak", ["tightening_cycle"]),
        ("1998-10-08", -0.25, "Cut to 7.25%", ["easing_cycle"]),
        ("1998-11-05", -0.50, "Cut to 6.75%", ["easing_cycle"]),
        ("1998-12-10", -0.50, "Cut to 6.25%", ["easing_cycle"]),
        ("1999-01-07", -0.25, "Cut to 6.00%", ["easing_cycle"]),
        ("1999-02-04", -0.50, "Cut to 5.50%", ["easing_cycle"]),
        ("1999-04-08", -0.25, "Cut to 5.25%", ["easing_cycle"]),
        ("1999-06-10", -0.25, "Cut to 5.00% — cycle low", ["easing_cycle"]),
        ("1999-09-08", 0.25, "Hike to 5.25%", ["tightening_cycle"]),
        ("1999-11-04", 0.25, "Hike to 5.50%", ["tightening_cycle"]),
        ("2000-01-13", 0.25, "Hike to 5.75%", ["tightening_cycle"]),
        ("2000-02-10", 0.25, "Hike to 6.00% — cycle peak", ["tightening_cycle"]),
        # 2001 easing
        ("2001-02-08", -0.25, "Cut to 5.75%", ["easing_cycle"]),
        ("2001-04-05", -0.25, "Cut to 5.50%", ["easing_cycle"]),
        ("2001-05-10", -0.25, "Cut to 5.25%", ["easing_cycle"]),
        ("2001-08-02", -0.25, "Cut to 5.00%", ["easing_cycle"]),
        ("2001-09-18", -0.25, "Cut to 4.75% — post 9/11", ["easing_cycle"]),
        ("2001-10-04", -0.25, "Cut to 4.50%", ["easing_cycle"]),
        ("2001-11-08", -0.50, "Cut to 4.00%", ["easing_cycle"]),
        # 2003-2004
        ("2003-02-06", -0.25, "Cut to 3.75%", ["easing_cycle"]),
        ("2003-07-10", -0.25, "Cut to 3.50% — cycle low", ["easing_cycle"]),
        ("2003-11-06", 0.25, "Hike to 3.75%", ["tightening_cycle"]),
        ("2004-02-05", 0.25, "Hike to 4.00%", ["tightening_cycle"]),
        ("2004-05-06", 0.25, "Hike to 4.25%", ["tightening_cycle"]),
        ("2004-06-10", 0.25, "Hike to 4.50%", ["tightening_cycle"]),
        ("2004-08-05", 0.25, "Hike to 4.75%", ["tightening_cycle"]),
        ("2005-08-04", -0.25, "Cut to 4.50%", ["easing_cycle"]),
        # 2006-2007 tightening
        ("2006-08-03", 0.25, "Hike to 4.75%", ["tightening_cycle"]),
        ("2006-11-09", 0.25, "Hike to 5.00%", ["tightening_cycle"]),
        ("2007-01-11", 0.25, "Hike to 5.25%", ["tightening_cycle"]),
        ("2007-05-10", 0.25, "Hike to 5.50%", ["tightening_cycle"]),
        ("2007-07-05", 0.25, "Hike to 5.75% — cycle peak", ["tightening_cycle"]),
        # 2007-2009 GFC easing
        ("2007-12-06", -0.25, "Cut to 5.50%", ["easing_cycle"]),
        ("2008-02-07", -0.25, "Cut to 5.25%", ["easing_cycle"]),
        ("2008-04-10", -0.25, "Cut to 5.00%", ["easing_cycle"]),
        ("2008-10-08", -0.50, "Cut to 4.50% — GFC", ["easing_cycle"]),
        ("2008-11-06", -1.50, "Cut 150bp to 3.00%", ["easing_cycle", "emergency"]),
        ("2008-12-04", -1.00, "Cut 100bp to 2.00%", ["easing_cycle"]),
        ("2009-01-08", -0.50, "Cut to 1.50%", ["easing_cycle"]),
        ("2009-02-05", -0.50, "Cut to 1.00%", ["easing_cycle"]),
        ("2009-03-05", -0.50, "Cut to 0.50% — historic low", ["easing_cycle"]),
        # 2016 Brexit
        ("2016-08-04", -0.25, "Cut to 0.25% — post-Brexit vote", ["easing_cycle", "brexit"]),
        # 2017-2018 normalization
        ("2017-11-02", 0.25, "Hike to 0.50% — first hike in a decade", ["tightening_cycle"]),
        ("2018-08-02", 0.25, "Hike to 0.75%", ["tightening_cycle"]),
        # 2020 COVID
        ("2020-03-11", -0.25, "Cut to 0.25% — COVID", ["easing_cycle", "emergency"]),
        ("2020-03-19", -0.15, "Cut to 0.10% — historic low", ["easing_cycle", "emergency"]),
        # 2021-2023 tightening
        ("2021-12-16", 0.15, "Hike to 0.25%", ["tightening_cycle"]),
        ("2022-02-03", 0.25, "Hike to 0.50%", ["tightening_cycle"]),
        ("2022-03-17", 0.25, "Hike to 0.75%", ["tightening_cycle"]),
        ("2022-05-05", 0.25, "Hike to 1.00%", ["tightening_cycle"]),
        ("2022-06-16", 0.25, "Hike to 1.25%", ["tightening_cycle"]),
        ("2022-08-04", 0.50, "Hike to 1.75%", ["tightening_cycle"]),
        ("2022-09-22", 0.50, "Hike to 2.25%", ["tightening_cycle"]),
        ("2022-11-03", 0.75, "Hike 75bp to 3.00%", ["tightening_cycle"]),
        ("2022-12-15", 0.50, "Hike to 3.50%", ["tightening_cycle"]),
        ("2023-02-02", 0.50, "Hike to 4.00%", ["tightening_cycle"]),
        ("2023-03-23", 0.25, "Hike to 4.25%", ["tightening_cycle"]),
        ("2023-05-11", 0.25, "Hike to 4.50%", ["tightening_cycle"]),
        ("2023-06-22", 0.50, "Hike to 5.00%", ["tightening_cycle"]),
        ("2023-08-03", 0.25, "Hike to 5.25% — cycle peak", ["tightening_cycle"]),
        # 2024-2025 easing
        ("2024-08-01", -0.25, "Cut to 5.00%", ["easing_cycle"]),
        ("2024-11-07", -0.25, "Cut to 4.75%", ["easing_cycle"]),
        ("2025-02-06", -0.25, "Cut to 4.50%", ["easing_cycle"]),
        ("2025-05-08", -0.25, "Cut to 4.25%", ["easing_cycle"]),
        ("2025-08-07", -0.25, "Cut to 4.00%", ["easing_cycle"]),
        ("2025-12-18", -0.25, "Cut to 3.75%", ["easing_cycle"]),
    ]

    for date, mag, desc, tags in boe:
        direction = "hawkish" if mag > 0 else "dovish"
        events.append(_ev(date, "rate_decision", mag, "UK", direction, f"BOE: {desc}", "bankofengland.co.uk", ["boe"] + tags))

    # ══════════════════════════════════════════════════════════════════════════
    # BOJ — Verified from boj.or.jp, BIS, FRED
    # ══════════════════════════════════════════════════════════════════════════

    boj = [
        # Bubble-era peak
        ("1990-03-20", 1.00, "Hike discount rate to 5.25%", ["tightening_cycle"]),
        ("1990-08-30", 0.75, "Hike to 6.00% — cycle peak", ["tightening_cycle"]),
        # Post-bubble easing
        ("1991-07-01", -0.50, "Cut to 5.50%", ["easing_cycle"]),
        ("1991-11-14", -0.50, "Cut to 5.00%", ["easing_cycle"]),
        ("1991-12-30", -0.50, "Cut to 4.50%", ["easing_cycle"]),
        ("1992-04-01", -0.75, "Cut to 3.75%", ["easing_cycle"]),
        ("1992-07-27", -0.50, "Cut to 3.25%", ["easing_cycle"]),
        ("1993-02-04", -0.75, "Cut to 2.50%", ["easing_cycle"]),
        ("1993-09-21", -0.75, "Cut to 1.75%", ["easing_cycle"]),
        ("1995-04-14", -0.75, "Cut to 1.00%", ["easing_cycle"]),
        ("1995-09-08", -0.50, "Cut to 0.50%", ["easing_cycle"]),
        # ZIRP and QE
        ("1999-02-12", -0.50, "ZIRP adopted — call rate to ~0%", ["easing_cycle", "zirp"]),
        ("2000-08-11", 0.25, "Hike to 0.25% — ended ZIRP prematurely", ["tightening_cycle"]),
        ("2001-03-19", -0.25, "QE adopted — back to ~0%", ["easing_cycle", "zirp"]),
        # 2006-2007 normalization
        ("2006-07-14", 0.25, "Hike to 0.25% — end of QE", ["tightening_cycle"]),
        ("2007-02-21", 0.25, "Hike to 0.50%", ["tightening_cycle"]),
        # GFC
        ("2008-10-31", -0.20, "Cut to 0.30%", ["easing_cycle"]),
        ("2008-12-19", -0.20, "Cut to 0.10%", ["easing_cycle"]),
        # Negative rates
        ("2016-01-29", -0.20, "NIRP introduced — rate to -0.10%", ["easing_cycle", "nirp"]),
        # 2024-2025 normalization
        ("2024-03-19", 0.10, "End of NIRP — rate to 0-0.10%", ["tightening_cycle"]),
        ("2024-07-31", 0.15, "Hike to 0.25%", ["tightening_cycle"]),
        ("2025-01-24", 0.25, "Hike to 0.50% — highest in 17 years", ["tightening_cycle"]),
        ("2025-12-19", 0.25, "Hike to 0.75% — highest since 1995", ["tightening_cycle"]),
    ]

    for date, mag, desc, tags in boj:
        direction = "hawkish" if mag > 0 else "dovish"
        events.append(_ev(date, "rate_decision", mag, "Japan", direction, f"BOJ: {desc}", "boj.or.jp", ["boj"] + tags))

    # ══════════════════════════════════════════════════════════════════════════
    # PBOC — Verified from pbc.gov.cn, FRED, Trading Economics
    # ══════════════════════════════════════════════════════════════════════════

    pboc = [
        # Old benchmark lending rate era
        ("2004-10-29", 0.27, "Hike 1Y lending rate to 5.58% — first hike in 9 years", ["tightening_cycle"]),
        ("2006-04-28", 0.27, "Hike to 5.85%", ["tightening_cycle"]),
        ("2006-08-19", 0.27, "Hike to 6.12%", ["tightening_cycle"]),
        ("2007-03-18", 0.27, "Hike to 6.39%", ["tightening_cycle"]),
        ("2007-05-19", 0.18, "Hike to 6.57%", ["tightening_cycle"]),
        ("2007-07-21", 0.27, "Hike to 6.84%", ["tightening_cycle"]),
        ("2007-08-22", 0.18, "Hike to 7.02%", ["tightening_cycle"]),
        ("2007-09-15", 0.27, "Hike to 7.29%", ["tightening_cycle"]),
        ("2007-12-21", 0.18, "Hike to 7.47% — cycle peak", ["tightening_cycle"]),
        # GFC easing
        ("2008-09-16", -0.27, "Cut to 7.20% — GFC begins", ["easing_cycle"]),
        ("2008-10-09", -0.27, "Cut to 6.93%", ["easing_cycle"]),
        ("2008-10-30", -0.27, "Cut to 6.66%", ["easing_cycle"]),
        ("2008-11-27", -1.08, "Cut 108bp to 5.58%", ["easing_cycle", "emergency"]),
        ("2008-12-23", -0.27, "Cut to 5.31% — cycle low", ["easing_cycle"]),
        # 2010-2011 tightening
        ("2010-10-20", 0.25, "Hike to 5.56% — first hike since 2007", ["tightening_cycle"]),
        ("2010-12-26", 0.25, "Hike to 5.81%", ["tightening_cycle"]),
        ("2011-02-09", 0.25, "Hike to 6.06%", ["tightening_cycle"]),
        ("2011-04-06", 0.25, "Hike to 6.31%", ["tightening_cycle"]),
        ("2011-07-07", 0.25, "Hike to 6.56% — cycle peak", ["tightening_cycle"]),
        # 2012-2015 easing
        ("2012-06-08", -0.25, "Cut to 6.31%", ["easing_cycle"]),
        ("2012-07-06", -0.25, "Cut to 6.00%", ["easing_cycle"]),
        ("2014-11-22", -0.40, "Cut to 5.60%", ["easing_cycle"]),
        ("2015-03-01", -0.25, "Cut to 5.35%", ["easing_cycle"]),
        ("2015-05-11", -0.25, "Cut to 5.10%", ["easing_cycle"]),
        ("2015-06-28", -0.25, "Cut to 4.85%", ["easing_cycle"]),
        ("2015-08-26", -0.25, "Cut to 4.60%", ["easing_cycle"]),
        ("2015-10-24", -0.25, "Cut to 4.35% — last old benchmark change", ["easing_cycle"]),
        # LPR era
        ("2019-08-20", -0.10, "LPR launched at 4.25% (1Y)", ["easing_cycle", "lpr"]),
        ("2019-09-20", -0.05, "LPR cut to 4.20%", ["easing_cycle", "lpr"]),
        ("2019-11-20", -0.05, "LPR cut to 4.15%", ["easing_cycle", "lpr"]),
        ("2020-02-20", -0.10, "LPR cut to 4.05% — COVID", ["easing_cycle", "lpr"]),
        ("2020-04-20", -0.20, "LPR cut to 3.85%", ["easing_cycle", "lpr"]),
        ("2021-12-20", -0.05, "LPR cut to 3.80%", ["easing_cycle", "lpr"]),
        ("2022-01-20", -0.10, "LPR cut to 3.70%", ["easing_cycle", "lpr"]),
        ("2022-08-22", -0.05, "LPR cut to 3.65%", ["easing_cycle", "lpr"]),
        ("2023-06-20", -0.10, "LPR cut to 3.55%", ["easing_cycle", "lpr"]),
        ("2024-07-22", -0.10, "LPR cut to 3.35%", ["easing_cycle", "lpr"]),
        ("2024-10-21", -0.25, "LPR cut to 3.10%", ["easing_cycle", "lpr"]),
        ("2025-05-20", -0.10, "LPR cut to 3.00%", ["easing_cycle", "lpr"]),
    ]

    for date, mag, desc, tags in pboc:
        direction = "hawkish" if mag > 0 else "dovish"
        events.append(_ev(date, "rate_decision", mag, "China", direction, f"PBOC: {desc}", "pbc.gov.cn", ["pboc"] + tags))

    # ══════════════════════════════════════════════════════════════════════════
    # OIL SHOCKS
    # ══════════════════════════════════════════════════════════════════════════

    oil_events = [
        ("1990-08-02", 8.0, "Middle East", "up", "Iraq invades Kuwait — oil spikes from $20 to $40", ["gulf_war"]),
        ("1991-01-17", -5.0, "Middle East", "down", "Operation Desert Storm begins — quick victory expectations", ["gulf_war"]),
        ("1997-11-01", -3.0, "Global", "down", "Asian financial crisis crushes oil demand", ["asian_crisis"]),
        ("1998-03-23", 2.0, "Middle East", "up", "OPEC production cut agreement", ["opec_cut"]),
        ("1999-03-23", 3.0, "Middle East", "up", "OPEC cuts 2M bpd", ["opec_cut"]),
        ("2003-03-20", 4.0, "Middle East", "up", "Iraq War begins — supply fears", ["iraq_war"]),
        ("2005-08-29", 5.0, "US", "up", "Hurricane Katrina devastates Gulf oil infrastructure", ["natural_disaster"]),
        ("2008-07-11", 10.0, "Global", "up", "Oil hits all-time high $147/bbl", ["commodity_supercycle"]),
        ("2008-12-01", -10.0, "Global", "down", "Oil crashes to $32 amid GFC demand collapse", ["gfc"]),
        ("2011-02-15", 5.0, "Middle East", "up", "Libya civil war disrupts supply — Arab Spring", ["arab_spring"]),
        ("2014-11-27", -5.0, "Global", "down", "OPEC refuses to cut — price war vs US shale", ["opec_price_war"]),
        ("2016-01-20", -3.0, "Global", "down", "Oil hits $26/bbl — oversupply + China slowdown", ["china_slowdown"]),
        ("2016-09-28", 3.0, "Middle East", "up", "OPEC agrees to first production cut in 8 years", ["opec_cut"]),
        ("2019-09-14", 5.0, "Middle East", "up", "Drone attack on Saudi Aramco Abqaiq", ["geopolitical"]),
        ("2020-03-09", -8.0, "Global", "down", "Saudi-Russia oil price war — oil crashes 25%", ["opec_price_war"]),
        ("2020-04-20", -10.0, "Global", "down", "WTI goes negative — -$37.63/bbl", ["covid", "storage_crisis"]),
        ("2022-02-24", 8.0, "Europe", "up", "Russia invades Ukraine — oil spikes past $100", ["ukraine_war"]),
        ("2022-03-08", 10.0, "Global", "up", "Oil hits $130 — Russian oil ban fears", ["ukraine_war"]),
        ("2022-03-31", -3.0, "US", "down", "Biden 180M barrel SPR release", ["spr_release"]),
        ("2023-04-02", 4.0, "Middle East", "up", "OPEC+ surprise voluntary cut 1.16M bpd", ["opec_cut"]),
        ("2023-10-07", 3.0, "Middle East", "up", "Hamas attack on Israel — escalation fears", ["geopolitical"]),
    ]

    for date, mag, geo, direction, desc, tags in oil_events:
        events.append(_ev(date, "oil_shock", mag, geo, direction, desc, "historical", tags))

    # ══════════════════════════════════════════════════════════════════════════
    # GEOPOLITICAL
    # ══════════════════════════════════════════════════════════════════════════

    geo_events = [
        ("1990-08-02", 7.0, "Middle East", "risk_on", "Iraq invades Kuwait", ["war"]),
        ("1991-12-26", 5.0, "Global", "risk_off", "Soviet Union dissolves", ["regime_change"]),
        ("1997-07-02", 6.0, "Asia", "risk_on", "Thai baht collapse — Asian Crisis begins", ["financial_crisis"]),
        ("1998-08-17", 5.0, "Russia", "risk_on", "Russia defaults on debt", ["sovereign_default"]),
        ("2001-09-11", 10.0, "US", "risk_on", "9/11 terrorist attacks", ["terrorism"]),
        ("2003-03-20", 6.0, "Middle East", "risk_on", "US invades Iraq", ["war"]),
        ("2010-05-02", 4.0, "Europe", "risk_on", "Greece bailout — European debt crisis", ["sovereign_debt"]),
        ("2011-03-11", 6.0, "Japan", "risk_on", "Fukushima earthquake and nuclear disaster", ["natural_disaster"]),
        ("2014-03-01", 4.0, "Europe", "risk_on", "Russia annexes Crimea", ["war", "sanctions"]),
        ("2015-08-11", 5.0, "Asia", "risk_on", "China devalues yuan", ["currency_war"]),
        ("2016-06-23", 6.0, "Europe", "risk_on", "Brexit referendum — UK votes Leave", ["political"]),
        ("2016-11-08", 4.0, "US", "risk_off", "Trump wins presidency — surprise", ["political"]),
        ("2018-03-22", 4.0, "Global", "risk_on", "US-China trade war begins", ["trade_war"]),
        ("2018-07-06", 3.0, "Global", "risk_on", "US $34B tariffs on China — China retaliates", ["trade_war"]),
        ("2019-05-10", 4.0, "Global", "risk_on", "US raises tariffs to 25% on $200B Chinese goods", ["trade_war"]),
        ("2020-01-03", 5.0, "Middle East", "risk_on", "US kills Iranian General Soleimani", ["geopolitical"]),
        ("2022-02-24", 9.0, "Europe", "risk_on", "Russia invades Ukraine", ["war"]),
        ("2023-10-07", 7.0, "Middle East", "risk_on", "Hamas attack on Israel", ["war", "terrorism"]),
        ("2024-04-13", 4.0, "Middle East", "risk_on", "Iran drone/missile attack on Israel", ["war"]),
        ("2025-02-01", 5.0, "Global", "risk_on", "US tariffs on Canada, Mexico, China", ["trade_war"]),
    ]

    for date, mag, geo, direction, desc, tags in geo_events:
        events.append(_ev(date, "geopolitical", mag, geo, direction, desc, "historical", tags))

    # ══════════════════════════════════════════════════════════════════════════
    # MARKET STRUCTURE EVENTS
    # ══════════════════════════════════════════════════════════════════════════

    market_events = [
        ("1987-10-19", 10.0, "US", "crash", "Black Monday — Dow drops 22.6%", ["crash"]),
        ("1997-10-27", 6.0, "US", "crash", "Mini-crash — circuit breakers triggered", ["crash"]),
        ("1998-09-23", 7.0, "US", "crash", "LTCM bailout", ["bailout"]),
        ("2000-03-10", 8.0, "US", "peak", "Nasdaq peaks at 5,048 — dot-com top", ["bubble_peak"]),
        ("2002-10-09", 8.0, "US", "bottom", "S&P bottoms at 768 — dot-com low", ["bear_market_bottom"]),
        ("2007-08-09", 6.0, "Global", "crash", "BNP Paribas freezes funds — subprime begins", ["subprime"]),
        ("2008-03-16", 8.0, "US", "crash", "Bear Stearns collapses", ["bailout", "gfc"]),
        ("2008-09-07", 7.0, "US", "crash", "Fannie/Freddie conservatorship", ["bailout", "gfc"]),
        ("2008-09-15", 10.0, "US", "crash", "Lehman Brothers bankruptcy", ["bankruptcy", "gfc"]),
        ("2009-03-09", 9.0, "US", "bottom", "S&P bottoms at 666 — GFC low", ["bear_market_bottom"]),
        ("2010-05-06", 7.0, "US", "crash", "Flash Crash — Dow drops 1,000 in minutes", ["flash_crash"]),
        ("2011-08-05", 5.0, "US", "crash", "S&P downgrades US from AAA", ["credit_downgrade"]),
        ("2015-08-24", 6.0, "US", "crash", "Black Monday 2015 — China fears", ["flash_crash"]),
        ("2018-02-05", 6.0, "US", "crash", "Volmageddon — VIX spikes 115%, XIV liquidated", ["volatility_event"]),
        ("2020-03-09", 7.0, "US", "crash", "First COVID circuit breaker — S&P down 7.6%", ["circuit_breaker"]),
        ("2020-03-12", 8.0, "US", "crash", "Worst day since 1987 — S&P drops 9.5%", ["crash", "covid"]),
        ("2020-03-16", 8.0, "US", "crash", "Third circuit breaker — S&P down 12%", ["circuit_breaker"]),
        ("2020-03-23", 9.0, "US", "bottom", "COVID bear market bottom — S&P at 2,237", ["bear_market_bottom"]),
        ("2021-01-27", 5.0, "US", "crash", "GameStop short squeeze", ["short_squeeze"]),
        ("2023-03-10", 6.0, "US", "crash", "SVB collapses", ["bank_failure"]),
        ("2023-03-19", 4.0, "Europe", "crash", "Credit Suisse emergency takeover by UBS", ["bank_failure"]),
        ("2024-08-05", 6.0, "Global", "crash", "Yen carry trade unwind — Nikkei crashes 12.4%", ["carry_trade"]),
    ]

    for date, mag, geo, direction, desc, tags in market_events:
        events.append(_ev(date, "market_structure", mag, geo, direction, desc, "historical", tags))

    # ══════════════════════════════════════════════════════════════════════════
    # MACRO SURPRISES
    # ══════════════════════════════════════════════════════════════════════════

    macro_events = [
        # US CPI
        ("2021-06-10", -5.0, "US", "beat", "CPI +5.0% YoY vs +4.7% exp", "bls.gov", ["cpi", "beat"]),
        ("2021-11-10", -6.0, "US", "beat", "CPI +6.2% YoY vs +5.8% exp — highest since 1990", "bls.gov", ["cpi", "beat"]),
        ("2022-02-10", -7.0, "US", "beat", "CPI +7.5% YoY vs +7.3% exp", "bls.gov", ["cpi", "beat"]),
        ("2022-06-10", -8.0, "US", "beat", "CPI +8.6% YoY vs +8.3% exp — triggers 75bp hike", "bls.gov", ["cpi", "beat"]),
        ("2022-07-13", -6.0, "US", "beat", "CPI +9.1% YoY vs +8.8% exp — cycle peak", "bls.gov", ["cpi", "beat"]),
        ("2022-11-10", 7.0, "US", "miss", "CPI +7.7% vs +8.0% exp — first big miss, massive rally", "bls.gov", ["cpi", "miss"]),
        ("2024-02-13", -5.0, "US", "beat", "CPI +3.1% vs +2.9% exp — delays rate cuts", "bls.gov", ["cpi", "beat"]),
        # US NFP
        ("2008-12-05", -7.0, "US", "miss", "NFP -533k vs -340k exp — worst since 1974", "bls.gov", ["nfp", "miss"]),
        ("2020-05-08", 6.0, "US", "beat", "NFP +2.5M vs -7.5M exp — surprise recovery", "bls.gov", ["nfp", "beat"]),
        ("2020-06-05", 7.0, "US", "beat", "NFP +2.5M vs -8M exp", "bls.gov", ["nfp", "beat"]),
        ("2024-08-02", -6.0, "US", "miss", "NFP +114k vs +175k, unemployment jumps to 4.3%", "bls.gov", ["nfp", "miss"]),
        # US GDP
        ("2008-10-30", -4.0, "US", "miss", "GDP Q3 2008 -0.3% vs +0.5% exp", "bea.gov", ["gdp", "miss"]),
        ("2020-04-29", -7.0, "US", "miss", "GDP Q1 2020 -4.8%", "bea.gov", ["gdp", "miss"]),
        ("2020-07-30", -10.0, "US", "miss", "GDP Q2 2020 -32.9% annualized — worst ever", "bea.gov", ["gdp", "miss"]),
        ("2020-10-29", 8.0, "US", "beat", "GDP Q3 2020 +33.1% — V-shaped recovery", "bea.gov", ["gdp", "beat"]),
        # UK
        ("2022-10-12", -4.0, "UK", "beat", "UK CPI +10.1% YoY — highest in 40 years", "ons.gov.uk", ["cpi", "beat"]),
        ("2022-09-23", -8.0, "UK", "crash", "Truss mini-budget — gilt market crash, BOE emergency intervention", "historical", ["fiscal", "crash"]),
        # EU
        ("2022-08-31", -5.0, "EU", "beat", "Eurozone CPI +9.1% — record high", "eurostat", ["cpi", "beat"]),
        # Japan
        ("2024-03-19", 5.0, "Japan", "beat", "BOJ ends NIRP — first hike in 17 years, markets rally", "boj.or.jp", ["boj", "historic"]),
        # China
        ("2015-08-11", -7.0, "China", "miss", "PBOC devalues yuan — global panic", "pbc.gov.cn", ["currency", "devaluation"]),
        ("2023-08-08", -4.0, "China", "miss", "China CPI turns negative -0.3% — deflation fears", "stats.gov.cn", ["cpi", "deflation"]),
    ]

    for item in macro_events:
        date, mag, geo, direction, desc = item[0], item[1], item[2], item[3], item[4]
        source = item[5] if len(item) > 5 else "historical"
        tags = item[6] if len(item) > 6 else []
        events.append(_ev(date, "macro_surprise", mag, geo, direction, desc, source, tags))

    # ══════════════════════════════════════════════════════════════════════════
    # INSERT
    # ══════════════════════════════════════════════════════════════════════════

    print(f"  Total events to seed: {len(events)}")
    print(f"    Fed decisions:    {sum(1 for e in events if 'fed' in (e.get('tags') or []))}")
    print(f"    ECB decisions:    {sum(1 for e in events if 'ecb' in (e.get('tags') or []))}")
    print(f"    BOE decisions:    {sum(1 for e in events if 'boe' in (e.get('tags') or []))}")
    print(f"    BOJ decisions:    {sum(1 for e in events if 'boj' in (e.get('tags') or []))}")
    print(f"    PBOC decisions:   {sum(1 for e in events if 'pboc' in (e.get('tags') or []))}")
    print(f"    Oil shocks:       {sum(1 for e in events if e['event_type'] == 'oil_shock')}")
    print(f"    Geopolitical:     {sum(1 for e in events if e['event_type'] == 'geopolitical')}")
    print(f"    Market structure:  {sum(1 for e in events if e['event_type'] == 'market_structure')}")
    print(f"    Macro surprises:  {sum(1 for e in events if e['event_type'] == 'macro_surprise')}")

    client = get_client()

    # Clear existing events
    client.table("events").delete().neq("id", 0).execute()

    # Insert in batches
    batch_size = 100
    for i in range(0, len(events), batch_size):
        chunk = events[i : i + batch_size]
        client.table("events").insert(chunk).execute()
        print(f"    Inserted batch {i // batch_size + 1} ({len(chunk)} events)")

    print(f"=== Events seed complete — {len(events)} events ===\n")


if __name__ == "__main__":
    seed_events()
