from __future__ import annotations

"""Automatic macro event detector.

Monitors key assets for significant price moves and threshold crossings,
then inserts new events into the database. Run daily (or on-demand) to
keep the events table current.

Usage:
    python -m backtesting.event_detector          # detect + insert
    python -m backtesting.event_detector --dry-run # preview only
"""

import asyncio
import sys
from datetime import date, timedelta

from backtesting.wrappers.marketdata import get_candles, get_quote
from backtesting.db import get_client

# ── Watchlist: assets to monitor for event detection ──────────────────────

WATCHLIST = {
    # Oil
    "USO": {"name": "Crude Oil (USO)", "event_type": "oil_shock", "geography": "Global"},
    # Volatility
    "UVXY": {"name": "VIX/Volatility (UVXY)", "event_type": "market_structure", "geography": "US"},
    # US equities
    "SPY": {"name": "S&P 500 (SPY)", "event_type": "market_structure", "geography": "US"},
    "QQQ": {"name": "NASDAQ 100 (QQQ)", "event_type": "market_structure", "geography": "US"},
    "IWM": {"name": "Russell 2000 (IWM)", "event_type": "market_structure", "geography": "US"},
    # Gold
    "GLD": {"name": "Gold (GLD)", "event_type": "macro_surprise", "geography": "Global"},
    # Bonds
    "TLT": {"name": "Long-term Treasuries (TLT)", "event_type": "macro_surprise", "geography": "US"},
    # International
    "EEM": {"name": "Emerging Markets (EEM)", "event_type": "market_structure", "geography": "Global"},
    "FXI": {"name": "China (FXI)", "event_type": "geopolitical", "geography": "China"},
}

# ── Thresholds for event detection ────────────────────────────────────────

# Daily move thresholds (absolute % change to trigger an event)
DAILY_MOVE_THRESHOLDS = {
    "SPY":  3.0,    # S&P drops/rallies 3%+ in a day
    "QQQ":  4.0,    # NASDAQ drops/rallies 4%+ in a day
    "IWM":  4.0,    # Russell drops/rallies 4%+
    "USO":  6.0,    # Oil moves 6%+ in a day
    "GLD":  3.0,    # Gold moves 3%+ in a day
    "TLT":  3.0,    # Bonds move 3%+ in a day
    "EEM":  4.0,    # Emerging markets 4%+
    "FXI":  5.0,    # China 5%+
    "UVXY": 25.0,   # VIX proxy spikes 25%+
}

# Multi-day streak thresholds (consecutive down/up days)
STREAK_THRESHOLDS = {
    "SPY":  5,  # 5 consecutive down days
    "QQQ":  5,
    "USO":  5,
}

# Price level alerts (when an asset crosses a notable level)
# These are checked against the CLOSE price
PRICE_LEVEL_ALERTS = {
    "USO": [
        {"level": 90,  "direction": "above", "desc": "Crude oil proxy above $90"},
        {"level": 100, "direction": "above", "desc": "Crude oil proxy above $100"},
        {"level": 40,  "direction": "below", "desc": "Crude oil proxy below $40"},
    ],
    "GLD": [
        {"level": 250, "direction": "above", "desc": "Gold ETF above $250 — all-time high territory"},
    ],
}

# Weekly move thresholds (5-day rolling)
WEEKLY_MOVE_THRESHOLDS = {
    "SPY":  5.0,
    "QQQ":  7.0,
    "USO":  10.0,
    "GLD":  5.0,
}


# ── Severity scoring ─────────────────────────────────────────────────────

def _severity(pct_change: float, asset: str) -> int:
    """Assign severity 1-10 based on magnitude of move."""
    abs_pct = abs(pct_change)
    if asset in ("UVXY",):
        if abs_pct >= 80: return 9
        if abs_pct >= 50: return 8
        if abs_pct >= 35: return 7
        return 6
    if abs_pct >= 10: return 9
    if abs_pct >= 7: return 8
    if abs_pct >= 5: return 7
    if abs_pct >= 3: return 6
    return 5


# ── Central Bank Meeting Calendar ─────────────────────────────────────────
# Published schedules — update annually. Dates are announcement dates.

CENTRAL_BANK_CALENDAR = {
    # ── G7 + Major Economies ──────────────────────────────────────────
    "FOMC": {
        "geography": "US",
        "bank": "Federal Reserve",
        "rate": 3.625,
        "expected": "HOLD 3.50-3.75",
        "currency": "USD",
        "dates": {
            "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
            "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
            "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
            "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
            "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
            "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
        },
    },
    "ECB": {
        "geography": "EU",
        "bank": "European Central Bank",
        "rate": 2.00,
        "expected": "HOLD 2.00",
        "currency": "EUR",
        "dates": {
            "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
            "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
            "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-11",
            "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17",
        },
    },
    "BOJ": {
        "geography": "Japan",
        "bank": "Bank of Japan",
        "rate": 0.75,
        "expected": "HOLD 0.75",
        "currency": "JPY",
        "dates": {
            "2025-01-24", "2025-03-14", "2025-05-01", "2025-06-17",
            "2025-07-31", "2025-09-19", "2025-10-31", "2025-12-19",
            "2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16",
            "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18",
        },
    },
    "BOE": {
        "geography": "UK",
        "bank": "Bank of England",
        "rate": 3.75,
        "expected": "HOLD 3.75",
        "currency": "GBP",
        "dates": {
            "2025-02-06", "2025-03-20", "2025-05-08", "2025-06-19",
            "2025-08-07", "2025-09-18", "2025-11-06", "2025-12-18",
            "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18",
            "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-17",
        },
    },
    "BOC": {
        "geography": "Canada",
        "bank": "Bank of Canada",
        "rate": 2.25,
        "expected": "HOLD 2.25",
        "currency": "CAD",
        "dates": {
            "2025-01-29", "2025-03-12", "2025-04-16", "2025-06-04",
            "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
            "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
            "2026-07-15", "2026-09-02", "2026-10-28", "2026-12-09",
        },
    },
    "PBOC": {
        "geography": "China",
        "bank": "People's Bank of China",
        "rate": 3.00,
        "expected": "HOLD 3.00",
        "currency": "CNY",
        "dates": {
            "2025-01-20", "2025-02-20", "2025-03-20", "2025-04-21",
            "2025-05-20", "2025-06-20", "2025-07-21", "2025-08-20",
            "2025-09-22", "2025-10-20", "2025-11-20", "2025-12-22",
            "2026-01-20", "2026-02-20", "2026-03-20", "2026-04-20",
            "2026-05-20", "2026-06-22", "2026-07-20", "2026-08-20",
            "2026-09-21", "2026-10-20", "2026-11-20", "2026-12-21",
        },
    },
    # ── Asia-Pacific ──────────────────────────────────────────────────
    "RBA": {
        "geography": "Australia",
        "bank": "Reserve Bank of Australia",
        "rate": 3.85,
        "expected": "HOLD 3.85",
        "currency": "AUD",
        "dates": {
            "2025-02-18", "2025-04-01", "2025-05-20", "2025-07-08",
            "2025-08-12", "2025-09-30", "2025-11-04", "2025-12-09",
            "2026-02-03", "2026-03-17", "2026-05-05", "2026-06-16",
            "2026-08-11", "2026-09-29", "2026-11-03", "2026-12-08",
        },
    },
    "RBNZ": {
        "geography": "New Zealand",
        "bank": "Reserve Bank of New Zealand",
        "rate": 2.25,
        "expected": "HOLD 2.25",
        "currency": "NZD",
        "dates": {
            "2025-02-19", "2025-04-09", "2025-05-28", "2025-07-09",
            "2025-08-20", "2025-10-08", "2025-11-26",
            "2026-02-18", "2026-04-08", "2026-05-27", "2026-07-08",
            "2026-09-02", "2026-10-28", "2026-12-09",
        },
    },
    "BOK": {
        "geography": "South Korea",
        "bank": "Bank of Korea",
        "rate": 2.75,
        "currency": "KRW",
        "dates": {
            "2025-01-16", "2025-02-27", "2025-04-17", "2025-05-29",
            "2025-07-10", "2025-08-21", "2025-10-16", "2025-11-27",
            "2026-01-15", "2026-02-26", "2026-04-09", "2026-05-28",
            "2026-07-09", "2026-08-27", "2026-10-15", "2026-11-26",
        },
    },
    "RBI": {
        "geography": "India",
        "bank": "Reserve Bank of India",
        "rate": 6.25,
        "currency": "INR",
        "dates": {
            "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-08",
            "2025-10-08", "2025-12-05",
            "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-07",
            "2026-10-09", "2026-12-04",
        },
    },
    "BI": {
        "geography": "Indonesia",
        "bank": "Bank Indonesia",
        "rate": 5.75,
        "currency": "IDR",
        "dates": {
            "2025-01-15", "2025-03-19", "2025-04-23", "2025-05-20",
            "2025-06-18", "2025-07-16", "2025-08-20", "2025-09-17",
            "2025-10-22", "2025-11-19", "2025-12-17",
            "2026-01-21", "2026-02-18", "2026-03-18", "2026-04-22",
            "2026-05-20", "2026-06-17", "2026-07-15", "2026-08-19",
            "2026-09-16", "2026-10-21", "2026-11-18", "2026-12-16",
        },
    },
    "BOT": {
        "geography": "Thailand",
        "bank": "Bank of Thailand",
        "rate": 2.00,
        "currency": "THB",
        "dates": {
            "2025-02-26", "2025-04-09", "2025-06-25", "2025-08-20",
            "2025-10-08", "2025-12-17",
            "2026-02-25", "2026-04-08", "2026-06-24", "2026-08-19",
            "2026-10-07", "2026-12-16",
        },
    },
    "MAS": {
        "geography": "Singapore",
        "bank": "Monetary Authority of Singapore",
        "rate": 3.44,
        "currency": "SGD",
        # MAS uses exchange-rate policy, meets semi-annually
        "dates": {
            "2025-04-14", "2025-10-14",
            "2026-04-13", "2026-10-13",
        },
    },
    # ── Latin America ─────────────────────────────────────────────────
    "BCB": {
        "geography": "Brazil",
        "bank": "Banco Central do Brasil",
        "rate": 15.00,
        "expected": "HOLD 15.00",
        "currency": "BRL",
        "dates": {
            "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
            "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-10",
            "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
            "2026-08-05", "2026-09-16", "2026-11-04", "2026-12-09",
        },
    },
    "BANXICO": {
        "geography": "Mexico",
        "bank": "Banco de México",
        "rate": 9.50,
        "currency": "MXN",
        "dates": {
            "2025-02-06", "2025-03-27", "2025-05-15", "2025-06-26",
            "2025-08-14", "2025-09-25", "2025-11-13", "2025-12-18",
            "2026-02-12", "2026-03-26", "2026-05-14", "2026-06-25",
            "2026-08-13", "2026-09-24", "2026-11-12", "2026-12-17",
        },
    },
    # ── Europe (non-ECB) ──────────────────────────────────────────────
    "SNB": {
        "geography": "Switzerland",
        "bank": "Swiss National Bank",
        "rate": 0.00,
        "currency": "CHF",
        "dates": {
            "2025-03-20", "2025-06-19", "2025-09-18", "2025-12-11",
            "2026-03-19", "2026-06-18", "2026-09-24", "2026-12-10",
        },
    },
    "RIKSBANK": {
        "geography": "Sweden",
        "bank": "Sveriges Riksbank",
        "rate": 1.75,
        "currency": "SEK",
        "dates": {
            "2025-01-29", "2025-03-20", "2025-05-07", "2025-06-26",
            "2025-09-04", "2025-11-06",
            "2026-01-29", "2026-03-19", "2026-05-07", "2026-06-17",
            "2026-08-20", "2026-09-24", "2026-11-04", "2026-12-16",
        },
    },
    "NORGES": {
        "geography": "Norway",
        "bank": "Norges Bank",
        "rate": 4.00,
        "currency": "NOK",
        "dates": {
            "2025-01-23", "2025-03-27", "2025-05-08", "2025-06-19",
            "2025-08-14", "2025-09-18", "2025-11-06", "2025-12-18",
            "2026-01-22", "2026-03-26", "2026-05-07", "2026-06-18",
            "2026-08-13", "2026-09-24", "2026-11-05", "2026-12-17",
        },
    },
    # ── Middle East / Africa ──────────────────────────────────────────
    "SARB": {
        "geography": "South Africa",
        "bank": "South African Reserve Bank",
        "rate": 7.50,
        "currency": "ZAR",
        "dates": {
            "2025-01-30", "2025-03-27", "2025-05-22", "2025-07-17",
            "2025-09-18", "2025-11-20",
            "2026-01-29", "2026-03-26", "2026-05-21", "2026-07-23",
            "2026-09-17", "2026-11-19",
        },
    },
    "TCMB": {
        "geography": "Turkey",
        "bank": "Central Bank of Turkey",
        "rate": 42.50,
        "expected": "CUT 42.00",
        "currency": "TRY",
        "dates": {
            "2025-01-23", "2025-03-06", "2025-04-17", "2025-06-05",
            "2025-07-17", "2025-08-21", "2025-09-18", "2025-10-23",
            "2025-11-20", "2025-12-25",
            "2026-01-22", "2026-03-05", "2026-04-16", "2026-05-28",
            "2026-07-09", "2026-08-20", "2026-09-17", "2026-10-22",
            "2026-11-19", "2026-12-24",
        },
    },
    # ── Other Major Economies ─────────────────────────────────────────
    "CBR": {
        "geography": "Russia",
        "bank": "Central Bank of Russia",
        "rate": 21.00,
        "currency": "RUB",
        "dates": {
            "2025-02-14", "2025-03-21", "2025-04-25", "2025-06-06",
            "2025-07-25", "2025-09-12", "2025-10-24", "2025-12-19",
            "2026-02-13", "2026-03-20", "2026-04-24", "2026-06-12",
            "2026-07-24", "2026-09-11", "2026-10-23", "2026-12-18",
        },
    },
    "BNM": {
        "geography": "Malaysia",
        "bank": "Bank Negara Malaysia",
        "rate": 3.00,
        "currency": "MYR",
        "dates": {
            "2025-01-22", "2025-03-06", "2025-05-08", "2025-07-10",
            "2025-09-04", "2025-11-06",
            "2026-01-21", "2026-03-05", "2026-05-07", "2026-07-09",
            "2026-09-03", "2026-11-05",
        },
    },
    "BSP": {
        "geography": "Philippines",
        "bank": "Bangko Sentral ng Pilipinas",
        "rate": 5.75,
        "currency": "PHP",
        "dates": {
            "2025-02-13", "2025-04-03", "2025-05-22", "2025-06-26",
            "2025-08-14", "2025-10-23", "2025-12-18",
            "2026-02-12", "2026-04-02", "2026-05-21", "2026-06-25",
            "2026-08-13", "2026-10-22", "2026-12-17",
        },
    },
}


def _detect_central_bank_events(lookback_days: int) -> list[dict]:
    """Check if any central bank meetings fell within the lookback window."""
    today = date.today()
    window_start = today - timedelta(days=lookback_days)
    detected = []

    for cb_key, info in CENTRAL_BANK_CALENDAR.items():
        for d_str in info["dates"]:
            d = date.fromisoformat(d_str)
            if window_start <= d <= today:
                detected.append({
                    "date": d_str,
                    "event_type": "rate_decision",
                    "magnitude": None,  # Unknown until confirmed — user can update
                    "geography": info["geography"],
                    "direction": None,
                    "description": f"{info['bank']} ({cb_key}) rate decision",
                    "source": "auto-detector:calendar",
                    "tags": ["auto", "rate_decision", cb_key.lower()],
                })
                print(f"  📅 {cb_key}: {d_str} — {info['bank']} rate decision", flush=True)

    return detected


# ── Detection logic ──────────────────────────────────────────────────────

async def detect_events(lookback_days: int = 7, dry_run: bool = False) -> list[dict]:
    """Scan watchlist assets for significant events in the last N days.

    Returns list of detected event dicts.
    """
    detected = []
    today = date.today()
    start = (today - timedelta(days=lookback_days + 10)).isoformat()  # extra buffer for weekends

    # ── Central bank calendar check ───────────────────────────────────
    print("Checking central bank calendar...", flush=True)
    cb_events = _detect_central_bank_events(lookback_days)
    detected.extend(cb_events)

    # ── Price-based detection ─────────────────────────────────────────
    print("Scanning price action...", flush=True)
    for sym, info in WATCHLIST.items():
        try:
            rows = await get_candles(sym, from_date=start)
            if not rows or len(rows) < 2:
                print(f"  {sym}: insufficient data, skipping")
                continue

            # Sort by date
            rows.sort(key=lambda r: r["date"])

            # Only look at the last lookback_days of trading days
            recent = rows[-lookback_days:] if len(rows) >= lookback_days else rows

            # ── 1. Daily big moves ────────────────────────────────────
            threshold = DAILY_MOVE_THRESHOLDS.get(sym)
            if threshold:
                for i, row in enumerate(recent):
                    # Find previous day's close
                    idx_in_full = rows.index(row)
                    if idx_in_full == 0:
                        continue
                    prev_close = rows[idx_in_full - 1]["close"]
                    if prev_close == 0:
                        continue
                    pct = ((row["close"] - prev_close) / prev_close) * 100

                    if abs(pct) >= threshold:
                        direction = "bullish" if pct > 0 else "bearish"
                        detected.append({
                            "date": row["date"],
                            "event_type": info["event_type"],
                            "magnitude": round(_severity(pct, sym)),
                            "geography": info["geography"],
                            "direction": direction,
                            "description": f"{info['name']} {'+' if pct > 0 else ''}{pct:.1f}% daily move",
                            "source": "auto-detector",
                            "tags": ["auto", "daily_move", sym.lower()],
                        })

            # ── 2. Consecutive streak detection ───────────────────────
            streak_threshold = STREAK_THRESHOLDS.get(sym)
            if streak_threshold and len(rows) >= streak_threshold + 1:
                # Check for consecutive down days ending in our lookback window
                for end_idx in range(len(rows) - 1, max(len(rows) - lookback_days - 1, streak_threshold), -1):
                    streak = 0
                    for j in range(end_idx, 0, -1):
                        if rows[j]["close"] < rows[j - 1]["close"]:
                            streak += 1
                        else:
                            break
                    if streak >= streak_threshold:
                        detected.append({
                            "date": rows[end_idx]["date"],
                            "event_type": info["event_type"],
                            "magnitude": streak,
                            "geography": info["geography"],
                            "direction": "bearish",
                            "description": f"{info['name']} — {streak} consecutive down days",
                            "source": "auto-detector",
                            "tags": ["auto", "streak", sym.lower()],
                        })
                        break  # Only report the most recent streak

                # Also check for consecutive up days
                for end_idx in range(len(rows) - 1, max(len(rows) - lookback_days - 1, streak_threshold), -1):
                    streak = 0
                    for j in range(end_idx, 0, -1):
                        if rows[j]["close"] > rows[j - 1]["close"]:
                            streak += 1
                        else:
                            break
                    if streak >= streak_threshold:
                        detected.append({
                            "date": rows[end_idx]["date"],
                            "event_type": info["event_type"],
                            "magnitude": streak,
                            "geography": info["geography"],
                            "direction": "bullish",
                            "description": f"{info['name']} — {streak} consecutive up days",
                            "source": "auto-detector",
                            "tags": ["auto", "streak", sym.lower()],
                        })
                        break

            # ── 3. Price level crossings ──────────────────────────────
            alerts = PRICE_LEVEL_ALERTS.get(sym, [])
            for alert in alerts:
                level = alert["level"]
                for i in range(1, len(recent)):
                    idx_in_full = rows.index(recent[i])
                    if idx_in_full == 0:
                        continue
                    prev = rows[idx_in_full - 1]["close"]
                    curr = recent[i]["close"]

                    crossed = False
                    if alert["direction"] == "above" and prev < level <= curr:
                        crossed = True
                    elif alert["direction"] == "below" and prev > level >= curr:
                        crossed = True

                    if crossed:
                        detected.append({
                            "date": recent[i]["date"],
                            "event_type": info["event_type"],
                            "magnitude": round(curr, 2),
                            "geography": info["geography"],
                            "direction": "bullish" if alert["direction"] == "above" else "bearish",
                            "description": alert["desc"] + f" (closed at ${curr:.2f})",
                            "source": "auto-detector",
                            "tags": ["auto", "price_level", sym.lower()],
                        })

            # ── 4. Weekly (5-day) big moves ───────────────────────────
            weekly_threshold = WEEKLY_MOVE_THRESHOLDS.get(sym)
            if weekly_threshold and len(rows) >= 6:
                # Check the most recent 5-day return
                latest = rows[-1]
                five_ago = rows[-6] if len(rows) >= 6 else rows[0]
                if five_ago["close"] > 0:
                    weekly_pct = ((latest["close"] - five_ago["close"]) / five_ago["close"]) * 100
                    if abs(weekly_pct) >= weekly_threshold:
                        direction = "bullish" if weekly_pct > 0 else "bearish"
                        detected.append({
                            "date": latest["date"],
                            "event_type": info["event_type"],
                            "magnitude": round(_severity(weekly_pct, sym)),
                            "geography": info["geography"],
                            "direction": direction,
                            "description": f"{info['name']} {'+' if weekly_pct > 0 else ''}{weekly_pct:.1f}% weekly move",
                            "source": "auto-detector",
                            "tags": ["auto", "weekly_move", sym.lower()],
                        })

            print(f"  {sym}: scanned {len(recent)} recent days", flush=True)
            await asyncio.sleep(1)  # Rate limit courtesy

        except Exception as e:
            print(f"  {sym}: ERROR — {e}", flush=True)

    # ── Deduplicate against existing events ────────────────────────────
    if detected:
        detected = _deduplicate(detected)

    # ── Insert into DB ─────────────────────────────────────────────────
    if detected and not dry_run:
        client = get_client()
        inserted = 0
        for evt in detected:
            try:
                client.table("events").insert(evt).execute()
                inserted += 1
            except Exception as e:
                print(f"  DB insert error: {e}", flush=True)
        print(f"\n✓ Inserted {inserted} new events", flush=True)
    elif detected and dry_run:
        print(f"\n[DRY RUN] Would insert {len(detected)} events:", flush=True)
        for evt in detected:
            print(f"  {evt['date']} | {evt['event_type']} | {evt['description']}", flush=True)
    else:
        print("\nNo significant events detected.", flush=True)

    return detected


def _deduplicate(events: list[dict]) -> list[dict]:
    """Remove events that already exist in the database (same date + similar description)."""
    client = get_client()

    # Get recent events from DB
    dates = set(e["date"] for e in events)
    min_date = min(dates)
    max_date = max(dates)

    existing = (
        client.table("events")
        .select("date, event_type, description")
        .gte("date", min_date)
        .lte("date", max_date)
        .execute()
        .data
    )

    existing_keys = set()
    for e in existing:
        # Key on date + first 30 chars of description for fuzzy match
        key = e["date"] + "|" + (e.get("description") or "")[:30].lower()
        existing_keys.add(key)

    new_events = []
    for evt in events:
        key = evt["date"] + "|" + (evt.get("description") or "")[:30].lower()
        if key not in existing_keys:
            new_events.append(evt)
            existing_keys.add(key)  # Prevent self-duplicates too
        else:
            print(f"  [skip] Already exists: {evt['date']} — {evt['description'][:50]}", flush=True)

    return new_events


# ── CLI entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    lookback = 7
    for arg in sys.argv[1:]:
        if arg.startswith("--lookback="):
            lookback = int(arg.split("=")[1])

    print(f"=== Event Detector (lookback={lookback} days, dry_run={dry_run}) ===", flush=True)
    results = asyncio.run(detect_events(lookback_days=lookback, dry_run=dry_run))
    print(f"\nTotal events detected: {len(results)}", flush=True)
