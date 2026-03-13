"""Condition definitions for the pattern scanner.

Each condition is a function that takes a pandas DataFrame of OHLCV data
(with computed columns) and returns a boolean Series marking signal dates.
"""
import numpy as np
import pandas as pd
from datetime import date

# ── Computed columns (added to DataFrame before condition evaluation) ────────

def add_computed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add all derived columns needed by conditions."""
    if df.empty or "date" not in df.columns:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Returns
    df["daily_return"] = df["close"].pct_change() * 100
    df["prev_close"] = df["close"].shift(1)
    df["gap_pct"] = (df["open"] / df["prev_close"] - 1) * 100

    # Streaks
    df["up_day"] = df["daily_return"] > 0
    df["down_day"] = df["daily_return"] < 0

    # Consecutive streak counter
    df["up_streak"] = 0
    df["down_streak"] = 0
    up_count = 0
    down_count = 0
    up_streaks = []
    down_streaks = []
    for ret in df["daily_return"]:
        if ret > 0:
            up_count += 1
            down_count = 0
        elif ret < 0:
            down_count += 1
            up_count = 0
        else:
            up_count = 0
            down_count = 0
        up_streaks.append(up_count)
        down_streaks.append(down_count)
    df["up_streak"] = up_streaks
    df["down_streak"] = down_streaks

    # Moving averages
    df["ma_50"] = df["close"].rolling(50).mean()
    df["ma_200"] = df["close"].rolling(200).mean()
    df["above_200ma"] = df["close"] > df["ma_200"]
    df["below_200ma"] = df["close"] < df["ma_200"]

    # 52-week high/low
    df["high_52w"] = df["high"].rolling(252).max()
    df["low_52w"] = df["low"].rolling(252).min()
    df["at_52w_high"] = df["high"] >= df["high_52w"]
    df["at_52w_low"] = df["low"] <= df["low_52w"]

    # Calendar features
    df["day_of_week"] = df["date"].dt.dayofweek  # 0=Mon, 4=Fri
    df["month"] = df["date"].dt.month
    df["day_of_month"] = df["date"].dt.day
    df["quarter"] = df["date"].dt.quarter

    # Trading day of month (ordinal within month)
    df["trading_day_of_month"] = df.groupby(df["date"].dt.to_period("M")).cumcount() + 1
    # Trading days left in month
    month_groups = df.groupby(df["date"].dt.to_period("M"))["date"].transform("count")
    df["trading_days_in_month"] = month_groups
    df["trading_day_from_end"] = df["trading_days_in_month"] - df["trading_day_of_month"] + 1

    # OPEX week: 3rd Friday of the month
    df["week_of_month"] = (df["date"].dt.day - 1) // 7 + 1
    df["is_opex_week"] = (df["week_of_month"] == 3) & (df["day_of_week"] <= 4)  # Mon-Fri of 3rd week

    # End of quarter
    df["is_quarter_end"] = (df["month"].isin([3, 6, 9, 12])) & (df["trading_day_from_end"] <= 5)

    return df


# ── Condition Functions ──────────────────────────────────────────────────────

def single_day_return(df: pd.DataFrame, threshold: float, direction: str = "up") -> pd.Series:
    """Daily return exceeds threshold. direction: 'up' or 'down'."""
    if direction == "up":
        return df["daily_return"] >= threshold
    return df["daily_return"] <= -abs(threshold)


def consecutive_up_days(df: pd.DataFrame, streak: int) -> pd.Series:
    """N consecutive up days."""
    return df["up_streak"] >= streak


def consecutive_down_days(df: pd.DataFrame, streak: int) -> pd.Series:
    """N consecutive down days."""
    return df["down_streak"] >= streak


def day_of_week(df: pd.DataFrame, dow: int) -> pd.Series:
    """Filter by day of week (0=Mon, 4=Fri)."""
    return df["day_of_week"] == dow


def month_of_year(df: pd.DataFrame, month: int) -> pd.Series:
    """Filter by month (1-12)."""
    return df["month"] == month


def opex_week(df: pd.DataFrame) -> pd.Series:
    """OPEX week (3rd week of month)."""
    return df["is_opex_week"]


def first_n_trading_days(df: pd.DataFrame, n: int = 5) -> pd.Series:
    """First N trading days of month."""
    return df["trading_day_of_month"] <= n


def last_n_trading_days(df: pd.DataFrame, n: int = 5) -> pd.Series:
    """Last N trading days of month."""
    return df["trading_day_from_end"] <= n


def end_of_quarter(df: pd.DataFrame) -> pd.Series:
    """Last 5 trading days of quarter."""
    return df["is_quarter_end"]


def gap_threshold(df: pd.DataFrame, threshold: float, direction: str = "up") -> pd.Series:
    """Gap up/down by a percentage threshold."""
    if direction == "up":
        return df["gap_pct"] >= threshold
    return df["gap_pct"] <= -abs(threshold)


def at_52w_high(df: pd.DataFrame) -> pd.Series:
    """Touching 52-week high."""
    return df["at_52w_high"]


def at_52w_low(df: pd.DataFrame) -> pd.Series:
    """Touching 52-week low."""
    return df["at_52w_low"]


def above_200ma(df: pd.DataFrame) -> pd.Series:
    """Price above 200-day moving average."""
    return df["above_200ma"]


def below_200ma(df: pd.DataFrame) -> pd.Series:
    """Price below 200-day moving average."""
    return df["below_200ma"]


def vix_level(df: pd.DataFrame, vix_df: pd.DataFrame, low: float, high: float) -> pd.Series:
    """VIX level within a range. Requires a separate VIX DataFrame."""
    vix = vix_df[["date", "close"]].rename(columns={"close": "vix_close"})
    vix["date"] = pd.to_datetime(vix["date"])
    merged = df.merge(vix, on="date", how="left")
    merged["vix_close"] = merged["vix_close"].ffill()
    return (merged["vix_close"] >= low) & (merged["vix_close"] < high)


def event_trigger(df: pd.DataFrame, event_dates: list[str]) -> pd.Series:
    """Signal on specific event dates."""
    event_set = set(pd.to_datetime(event_dates).date)
    return df["date"].dt.date.isin(event_set)


def fed_decision_week(df: pd.DataFrame, fed_dates: list[str]) -> pd.Series:
    """Week of a Fed decision. Matches Mon-Fri of the week containing a Fed date."""
    fed_dt = pd.to_datetime(fed_dates)
    # Get the Monday of each Fed week
    fed_weeks = set()
    for d in fed_dt:
        monday = d - pd.Timedelta(days=d.weekday())
        for i in range(5):
            fed_weeks.add((monday + pd.Timedelta(days=i)).date())
    return df["date"].dt.date.isin(fed_weeks)


# ── Condition Registry ───────────────────────────────────────────────────────

CONDITION_REGISTRY = {
    "single_day_return_up": {
        "fn": lambda df, params: single_day_return(df, params["threshold"], "up"),
        "params": {"threshold": "float (e.g. 1.0 for 1%)"},
        "description": "Daily return >= threshold%",
    },
    "single_day_return_down": {
        "fn": lambda df, params: single_day_return(df, params["threshold"], "down"),
        "params": {"threshold": "float (e.g. 2.0 for -2%)"},
        "description": "Daily return <= -threshold%",
    },
    "consecutive_up_days": {
        "fn": lambda df, params: consecutive_up_days(df, params["streak"]),
        "params": {"streak": "int (1-10)"},
        "description": "N consecutive up days",
    },
    "consecutive_down_days": {
        "fn": lambda df, params: consecutive_down_days(df, params["streak"]),
        "params": {"streak": "int (1-10)"},
        "description": "N consecutive down days",
    },
    "day_of_week": {
        "fn": lambda df, params: day_of_week(df, params["dow"]),
        "params": {"dow": "int (0=Mon, 4=Fri)"},
        "description": "Specific day of week",
    },
    "month_of_year": {
        "fn": lambda df, params: month_of_year(df, params["month"]),
        "params": {"month": "int (1-12)"},
        "description": "Specific month",
    },
    "opex_week": {
        "fn": lambda df, params: opex_week(df),
        "params": {},
        "description": "Options expiration week (3rd Friday week)",
    },
    "first_n_trading_days": {
        "fn": lambda df, params: first_n_trading_days(df, params.get("n", 5)),
        "params": {"n": "int (default 5)"},
        "description": "First N trading days of month",
    },
    "last_n_trading_days": {
        "fn": lambda df, params: last_n_trading_days(df, params.get("n", 5)),
        "params": {"n": "int (default 5)"},
        "description": "Last N trading days of month",
    },
    "end_of_quarter": {
        "fn": lambda df, params: end_of_quarter(df),
        "params": {},
        "description": "Last 5 trading days of quarter",
    },
    "gap_up": {
        "fn": lambda df, params: gap_threshold(df, params["threshold"], "up"),
        "params": {"threshold": "float (e.g. 1.0 for 1%)"},
        "description": "Gap up by threshold%",
    },
    "gap_down": {
        "fn": lambda df, params: gap_threshold(df, params["threshold"], "down"),
        "params": {"threshold": "float (e.g. 1.0 for -1%)"},
        "description": "Gap down by threshold%",
    },
    "at_52w_high": {
        "fn": lambda df, params: at_52w_high(df),
        "params": {},
        "description": "At 52-week high",
    },
    "at_52w_low": {
        "fn": lambda df, params: at_52w_low(df),
        "params": {},
        "description": "At 52-week low",
    },
    "above_200ma": {
        "fn": lambda df, params: above_200ma(df),
        "params": {},
        "description": "Price above 200-day moving average",
    },
    "below_200ma": {
        "fn": lambda df, params: below_200ma(df),
        "params": {},
        "description": "Price below 200-day moving average",
    },
    "vix_bucket": {
        "fn": lambda df, params: vix_level(df, params["vix_df"], params["low"], params["high"]),
        "params": {"low": "float", "high": "float", "vix_df": "DataFrame"},
        "description": "VIX level within [low, high)",
    },
    "event_trigger": {
        "fn": lambda df, params: event_trigger(df, params["dates"]),
        "params": {"dates": "list of date strings"},
        "description": "Signal on specific event dates",
    },
    "fed_decision_week": {
        "fn": lambda df, params: fed_decision_week(df, params["fed_dates"]),
        "params": {"fed_dates": "list of date strings"},
        "description": "Week of a Fed rate decision",
    },
}
