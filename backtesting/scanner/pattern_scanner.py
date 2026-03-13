from __future__ import annotations

"""Core pattern scanner with cross-asset support and earnings exclusion.

Supports:
- PRIMARY condition on any asset (signal source)
- OPTIONAL stacked secondary filters (AND logic)
- FUNDAMENTAL filters (P/E, market cap, sector, etc.)
- TARGET asset for forward returns (can differ from signal asset)
- Earnings exclusion toggle (±3 trading days around earnings by default)
- Cross-asset patterns (e.g., DXY up > 0.5% → SOFI forward returns)
"""
import hashlib
import json
from datetime import date

import numpy as np
import pandas as pd

from backtesting.db import (
    fetch_earnings_dates,
    fetch_earnings_dates_bulk,
    fetch_events,
    fetch_ohlcv,
    fetch_ohlcv_batch,
    fetch_ohlcv_multi,
    get_client,
)
from backtesting.scanner.conditions import CONDITION_REGISTRY, add_computed_columns


# ── Forward return calculators ───────────────────────────────────────────────

def compute_forward_returns(
    df: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
    horizons: list[int] = [1, 2, 5, 10],
) -> pd.DataFrame:
    """Given a target asset DataFrame and signal dates, compute forward returns.

    Returns a DataFrame with columns: signal_date, d1, d2, d5, d10 (returns in %).
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    results = []
    date_to_idx = {d: i for i, d in enumerate(df["date"])}

    for sig_date in signal_dates:
        if sig_date not in date_to_idx:
            # Find the next available trading day
            future = df[df["date"] >= sig_date]
            if future.empty:
                continue
            sig_date = future.iloc[0]["date"]
            if sig_date not in date_to_idx:
                continue

        idx = date_to_idx[sig_date]
        entry_price = df.iloc[idx]["close"]
        row = {"signal_date": sig_date}

        for h in horizons:
            target_idx = idx + h
            if target_idx < len(df):
                exit_price = df.iloc[target_idx]["close"]
                row[f"d{h}"] = ((exit_price / entry_price) - 1) * 100
            else:
                row[f"d{h}"] = np.nan

        results.append(row)

    return pd.DataFrame(results)


def compute_forward_returns_fast(
    df: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
    horizons: list[int] = [1, 2, 5, 10],
) -> pd.DataFrame:
    """Vectorized forward returns — ~40x faster than the loop version.

    Uses numpy array indexing instead of per-date Python loops.
    Signal dates not found in target are dropped (no snap-to-next-day).
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"].values
    dates = df["date"].values
    date_to_idx = pd.Series(np.arange(len(dates)), index=dates)

    # Only keep signal dates that exist in the target
    common = signal_dates[signal_dates.isin(date_to_idx.index)]
    if common.empty:
        return pd.DataFrame()

    idxs = date_to_idx.loc[common].values.astype(int)
    entry_prices = close[idxs]

    result_data = {"signal_date": common.values}
    for h in horizons:
        target_idxs = idxs + h
        valid = target_idxs < len(close)
        returns = np.full(len(idxs), np.nan)
        returns[valid] = ((close[target_idxs[valid]] / entry_prices[valid]) - 1) * 100
        result_data[f"d{h}"] = returns

    return pd.DataFrame(result_data)


# ── Earnings exclusion ───────────────────────────────────────────────────────

def get_earnings_exclusion_mask(
    df: pd.DataFrame,
    ticker: str,
    window: int = 3,
) -> pd.Series:
    """Returns a boolean Series where True = date should be EXCLUDED (near earnings).

    Excludes ±window trading days around each earnings date.
    """
    earnings = fetch_earnings_dates(ticker)
    if not earnings:
        return pd.Series(False, index=df.index)

    df_dates = pd.to_datetime(df["date"])
    earnings_dt = pd.to_datetime(earnings)

    exclude = pd.Series(False, index=df.index)
    for ed in earnings_dt:
        # Find the index of the earnings date (or nearest)
        diffs = (df_dates - ed).abs()
        nearest_idx = diffs.idxmin()
        low = max(0, nearest_idx - window)
        high = min(len(df) - 1, nearest_idx + window)
        exclude.iloc[low : high + 1] = True

    return exclude


# ── Main Scanner ─────────────────────────────────────────────────────────────

def scan_pattern(
    primary_ticker: str,
    primary_conditions: list[dict],
    target_ticker: str | None = None,
    secondary_conditions: list[dict] | None = None,
    fundamental_filters: dict | None = None,
    exclude_earnings: bool = True,
    earnings_window: int = 3,
    horizons: list[int] = [1, 2, 5, 10],
) -> dict:
    """Run a pattern scan.

    Args:
        primary_ticker: Asset to evaluate conditions on (signal source).
        primary_conditions: List of condition dicts, each with:
            {"type": "condition_name", "params": {param dict}}
        target_ticker: Asset to measure forward returns on. Defaults to primary_ticker.
        secondary_conditions: Additional conditions to stack (AND logic).
        fundamental_filters: Dict with keys like pe_range, market_cap_tier, sector, etc.
        exclude_earnings: Whether to exclude ±3 trading days around earnings.
        earnings_window: Number of trading days to exclude around earnings.
        horizons: Forward return periods in trading days.

    Returns:
        Dict with scan results.
    """
    if target_ticker is None:
        target_ticker = primary_ticker

    # 1. Fetch OHLCV data
    primary_data = fetch_ohlcv(primary_ticker)
    if not primary_data:
        return {"error": f"No data found for primary ticker: {primary_ticker}"}

    primary_df = pd.DataFrame(primary_data)
    primary_df = add_computed_columns(primary_df)

    # 2. Evaluate primary conditions
    masks = []
    condition_descriptions = []

    for cond in primary_conditions:
        cond_type = cond["type"]
        params = cond.get("params", {})

        if cond_type not in CONDITION_REGISTRY:
            return {"error": f"Unknown condition type: {cond_type}"}

        reg = CONDITION_REGISTRY[cond_type]

        # Handle special conditions that need external data
        if cond_type == "vix_bucket":
            vix_data = fetch_ohlcv("VIX")
            if vix_data:
                params["vix_df"] = pd.DataFrame(vix_data)
            else:
                return {"error": "VIX data not found in database. Run EODHD seed first."}

        if cond_type == "fed_decision_week":
            fed_events = fetch_events(event_type="rate_decision")
            params["fed_dates"] = [e["date"] for e in fed_events]

        if cond_type == "event_trigger":
            if "event_type" in params:
                events = fetch_events(event_type=params["event_type"])
                params["dates"] = [e["date"] for e in events]

        mask = reg["fn"](primary_df, params)
        masks.append(mask)
        condition_descriptions.append({"type": cond_type, "params": {k: v for k, v in params.items() if k not in ("vix_df",)}})

    # 3. Evaluate secondary conditions (on primary ticker by default)
    if secondary_conditions:
        for cond in secondary_conditions:
            cond_type = cond["type"]
            params = cond.get("params", {})
            cond_ticker = cond.get("ticker", primary_ticker)

            if cond_ticker != primary_ticker:
                # Cross-asset secondary condition
                sec_data = fetch_ohlcv(cond_ticker)
                if not sec_data:
                    return {"error": f"No data for secondary ticker: {cond_ticker}"}
                sec_df = pd.DataFrame(sec_data)
                sec_df = add_computed_columns(sec_df)
                # Align dates
                sec_df["date"] = pd.to_datetime(sec_df["date"])
                date_mask_map = dict(zip(sec_df["date"], CONDITION_REGISTRY[cond_type]["fn"](sec_df, params)))
                mask = primary_df["date"].map(lambda d: date_mask_map.get(d, False))
            else:
                if cond_type == "vix_bucket":
                    vix_data = fetch_ohlcv("VIX")
                    params["vix_df"] = pd.DataFrame(vix_data) if vix_data else pd.DataFrame()
                mask = CONDITION_REGISTRY[cond_type]["fn"](primary_df, params)

            masks.append(mask)
            condition_descriptions.append({"type": cond_type, "ticker": cond_ticker, "params": {k: v for k, v in params.items() if k not in ("vix_df",)}})

    # 4. Combine all conditions (AND)
    if not masks:
        return {"error": "No conditions specified"}

    combined = masks[0]
    for m in masks[1:]:
        # Align lengths
        combined = combined & m.reindex(combined.index, fill_value=False)

    # 5. Apply fundamental filters
    if fundamental_filters and target_ticker:
        client = get_client()
        q = client.table("fundamentals").select("*").eq("ticker", target_ticker)
        fund_data = q.execute().data
        if fund_data:
            fund_df = pd.DataFrame(fund_data)
            fund_df["date"] = pd.to_datetime(fund_df["date"])

            if "pe_min" in fundamental_filters or "pe_max" in fundamental_filters:
                pe_min = fundamental_filters.get("pe_min", -999999)
                pe_max = fundamental_filters.get("pe_max", 999999)
                valid_dates = fund_df[
                    (fund_df["pe_ratio"] >= pe_min) & (fund_df["pe_ratio"] <= pe_max)
                ]["date"]
                combined = combined & primary_df["date"].isin(valid_dates)

            if "sector" in fundamental_filters:
                valid_dates = fund_df[fund_df["sector"] == fundamental_filters["sector"]]["date"]
                if not valid_dates.empty:
                    combined = combined & primary_df["date"].isin(valid_dates)

            if "market_cap_min" in fundamental_filters:
                valid_dates = fund_df[
                    fund_df["market_cap"] >= fundamental_filters["market_cap_min"]
                ]["date"]
                combined = combined & primary_df["date"].isin(valid_dates)

    # 6. Apply earnings exclusion on TARGET ticker
    if exclude_earnings and target_ticker:
        target_data = fetch_ohlcv(target_ticker) if target_ticker != primary_ticker else primary_data
        if target_data:
            target_df = pd.DataFrame(target_data)
            target_df = add_computed_columns(target_df)
            if not target_df.empty and "date" in target_df.columns:
                earnings_mask = get_earnings_exclusion_mask(target_df, target_ticker, earnings_window)
                # Map exclusion back to primary dates
                target_dates_excluded = set(target_df[earnings_mask]["date"].dt.date)
                earnings_exclude = primary_df["date"].dt.date.isin(target_dates_excluded)
                combined = combined & ~earnings_exclude

    # 7. Get signal dates
    signal_dates = primary_df.loc[combined, "date"]

    if signal_dates.empty:
        return {
            "pattern_id": _pattern_id(condition_descriptions, target_ticker),
            "conditions": condition_descriptions,
            "primary_ticker": primary_ticker,
            "target_ticker": target_ticker,
            "sample_size": 0,
            "statistically_reliable": False,
            "message": "No matching signals found",
        }

    # 8. Compute forward returns on TARGET asset
    if target_ticker != primary_ticker:
        target_data = fetch_ohlcv(target_ticker)
        if not target_data:
            return {"error": f"No data for target ticker: {target_ticker}"}
        target_df = pd.DataFrame(target_data)
    else:
        target_df = pd.DataFrame(primary_data)

    returns_df = compute_forward_returns(target_df, signal_dates, horizons)

    if returns_df.empty:
        return {
            "pattern_id": _pattern_id(condition_descriptions, target_ticker),
            "conditions": condition_descriptions,
            "primary_ticker": primary_ticker,
            "target_ticker": target_ticker,
            "sample_size": 0,
            "statistically_reliable": False,
            "message": "No forward returns could be computed",
        }

    # 9. Calculate statistics
    sample_size = len(returns_df)
    stats = {"sample_size": sample_size, "statistically_reliable": sample_size >= 30}

    for h in horizons:
        col = f"d{h}"
        vals = returns_df[col].dropna()
        if len(vals) > 0:
            stats[f"avg_return_d{h}"] = round(vals.mean(), 4)
            stats[f"median_return_d{h}"] = round(vals.median(), 4)
            stats[f"win_rate_d{h}"] = round((vals > 0).mean() * 100, 2)
            stats[f"std_d{h}"] = round(vals.std(), 4)
            # Sharpe (annualized from daily, rough)
            if vals.std() > 0:
                stats[f"sharpe_d{h}"] = round((vals.mean() / vals.std()) * np.sqrt(252 / h), 4)
            else:
                stats[f"sharpe_d{h}"] = 0.0
        else:
            stats[f"avg_return_d{h}"] = None
            stats[f"median_return_d{h}"] = None
            stats[f"win_rate_d{h}"] = None

    # 10. Build result
    pattern_id = _pattern_id(condition_descriptions, target_ticker)

    # Return distribution for histogram
    distribution = {}
    for h in horizons:
        col = f"d{h}"
        vals = returns_df[col].dropna().tolist()
        distribution[f"d{h}"] = [round(v, 4) for v in vals]

    result = {
        "pattern_id": pattern_id,
        "conditions": condition_descriptions,
        "primary_ticker": primary_ticker,
        "target_ticker": target_ticker,
        "exclude_earnings": exclude_earnings,
        **stats,
        "distribution": distribution,
        "signal_dates": [d.strftime("%Y-%m-%d") for d in returns_df["signal_date"]],
    }

    # 11. Save to backtest_results
    _save_result(result, horizons)

    return result


def _pattern_id(conditions: list[dict], target_ticker: str) -> str:
    """Generate a deterministic pattern ID from conditions + target."""
    payload = json.dumps({"conditions": conditions, "target": target_ticker}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _save_result(result: dict, horizons: list[int]):
    """Persist result to backtest_results table."""
    try:
        client = get_client()
        row = {
            "pattern_id": result["pattern_id"],
            "conditions": json.dumps(result["conditions"]),
            "ticker": result["target_ticker"],
            "asset_class": "",  # Filled by caller if needed
            "sample_size": result["sample_size"],
            "win_rate": result.get("win_rate_d5"),
            "avg_return_d1": result.get("avg_return_d1"),
            "avg_return_d2": result.get("avg_return_d2"),
            "avg_return_d5": result.get("avg_return_d5"),
            "avg_return_d10": result.get("avg_return_d10"),
            "median_return": result.get("median_return_d5"),
            "sharpe": result.get("sharpe_d5"),
        }
        client.table("backtest_results").upsert(row, on_conflict="pattern_id").execute()
    except Exception as e:
        print(f"Warning: Could not save backtest result: {e}")


# ── Batch scanner for multi-ticker scans ─────────────────────────────────────

def scan_universe(
    primary_ticker: str,
    primary_conditions: list[dict],
    target_tickers: list[str],
    secondary_conditions: list[dict] | None = None,
    exclude_earnings: bool = True,
    earnings_window: int = 3,
    horizons: list[int] = [1, 2, 5, 10],
) -> list[dict]:
    """Run a pattern scan across multiple target tickers.

    Optimized: evaluates primary conditions ONCE, bulk-fetches all target
    OHLCV + earnings data, then computes forward returns per target with
    zero additional DB calls.
    """
    import time
    t0 = time.time()

    # ── 1. Fetch & evaluate primary conditions ONCE ──────────────────────
    primary_data = fetch_ohlcv(primary_ticker)
    if not primary_data:
        return [{"error": f"No data for primary ticker: {primary_ticker}"}]

    primary_df = pd.DataFrame(primary_data)
    primary_df = add_computed_columns(primary_df)

    masks = []
    condition_descriptions = []

    for cond in primary_conditions:
        cond_type = cond["type"]
        params = cond.get("params", {})

        if cond_type not in CONDITION_REGISTRY:
            return [{"error": f"Unknown condition type: {cond_type}"}]

        reg = CONDITION_REGISTRY[cond_type]

        # Handle special conditions (fetched once, not per-target)
        if cond_type == "vix_bucket":
            vix_data = fetch_ohlcv("VIX")
            if vix_data:
                params["vix_df"] = pd.DataFrame(vix_data)
            else:
                return [{"error": "VIX data not found. Run EODHD seed first."}]

        if cond_type == "fed_decision_week":
            fed_events = fetch_events(event_type="rate_decision")
            params["fed_dates"] = [e["date"] for e in fed_events]

        if cond_type == "event_trigger" and "event_type" in params:
            events = fetch_events(event_type=params["event_type"])
            params["dates"] = [e["date"] for e in events]

        mask = reg["fn"](primary_df, params)
        masks.append(mask)
        condition_descriptions.append({
            "type": cond_type,
            "params": {k: v for k, v in params.items() if k not in ("vix_df",)},
        })

    # ── 2. Secondary conditions (fetched once) ───────────────────────────
    if secondary_conditions:
        _sec_cache: dict[str, list[dict]] = {}
        for cond in secondary_conditions:
            cond_type = cond["type"]
            params = cond.get("params", {})
            cond_ticker = cond.get("ticker", primary_ticker)

            if cond_ticker != primary_ticker:
                if cond_ticker not in _sec_cache:
                    sec_data = fetch_ohlcv(cond_ticker)
                    if not sec_data:
                        return [{"error": f"No data for secondary ticker: {cond_ticker}"}]
                    _sec_cache[cond_ticker] = sec_data
                sec_df = pd.DataFrame(_sec_cache[cond_ticker])
                sec_df = add_computed_columns(sec_df)
                sec_df["date"] = pd.to_datetime(sec_df["date"])
                date_mask_map = dict(zip(sec_df["date"], CONDITION_REGISTRY[cond_type]["fn"](sec_df, params)))
                mask = primary_df["date"].map(lambda d: date_mask_map.get(d, False))
            else:
                if cond_type == "vix_bucket":
                    vix_data = fetch_ohlcv("VIX")
                    params["vix_df"] = pd.DataFrame(vix_data) if vix_data else pd.DataFrame()
                mask = CONDITION_REGISTRY[cond_type]["fn"](primary_df, params)

            masks.append(mask)
            condition_descriptions.append({
                "type": cond_type,
                "ticker": cond_ticker,
                "params": {k: v for k, v in params.items() if k not in ("vix_df",)},
            })

    # ── 3. Combine all masks → signal dates ──────────────────────────────
    if not masks:
        return [{"error": "No conditions specified"}]

    combined = masks[0]
    for m in masks[1:]:
        combined = combined & m.reindex(combined.index, fill_value=False)

    base_signal_dates = primary_df.loc[combined, "date"]

    if base_signal_dates.empty:
        return [{
            "pattern_id": _pattern_id(condition_descriptions, t),
            "conditions": condition_descriptions,
            "primary_ticker": primary_ticker,
            "target_ticker": t,
            "sample_size": 0,
            "statistically_reliable": False,
            "message": "No matching signals found",
        } for t in target_tickers]

    t_cond = time.time()
    print(f"  Conditions evaluated: {len(base_signal_dates)} signals in {t_cond - t0:.1f}s")

    # ── 4. Bulk-fetch ALL target OHLCV (parallel, minimal columns) ───────
    # Only need ticker + date + close for forward returns
    min_date = pd.Timestamp(base_signal_dates.min()).strftime("%Y-%m-%d")
    targets_to_fetch = [t for t in target_tickers if t != primary_ticker]

    print(f"  Bulk-fetching OHLCV for {len(targets_to_fetch)} targets...")
    # Scale concurrency with universe size: 15 for small, 30 for large
    workers = 30 if len(targets_to_fetch) > 500 else 15
    bulk_ohlcv = fetch_ohlcv_batch(
        targets_to_fetch,
        start_date=min_date,
        columns="ticker,date,close",
        max_workers=workers,
    ) if targets_to_fetch else {}

    # Include primary data if it's also a target
    if primary_ticker in target_tickers:
        bulk_ohlcv[primary_ticker] = primary_data

    t_fetch = time.time()
    print(f"  Fetched {len(bulk_ohlcv)} tickers in {t_fetch - t_cond:.1f}s")

    # ── 5. Bulk-fetch ALL earnings dates ─────────────────────────────────
    if exclude_earnings:
        bulk_earnings = fetch_earnings_dates_bulk(target_tickers)
        t_earn = time.time()
        print(f"  Fetched earnings for {len(bulk_earnings)} tickers in {t_earn - t_fetch:.1f}s")
    else:
        bulk_earnings = {}

    # ── 6. Process each target (pure computation, no DB calls) ───────────
    # Pre-filter: skip tickers with too few data points (< 250 days = ~1yr)
    min_data_points = 250
    valid_targets = [t for t in target_tickers if len(bulk_ohlcv.get(t, [])) >= min_data_points]
    skipped = len(target_tickers) - len(valid_targets)
    if skipped > 0:
        print(f"  Skipping {skipped} tickers with <{min_data_points} data points")
    print(f"  Computing forward returns for {len(valid_targets)} targets...")
    results = []
    save_queue = []

    for target in valid_targets:
        target_rows = bulk_ohlcv.get(target)
        if not target_rows:
            continue

        signal_dates = base_signal_dates.copy()

        # Apply earnings exclusion (pure computation)
        if exclude_earnings:
            earnings = bulk_earnings.get(target, [])
            if earnings:
                target_df_tmp = pd.DataFrame(target_rows)
                target_df_tmp["date"] = pd.to_datetime(target_df_tmp["date"])
                target_df_tmp = target_df_tmp.sort_values("date").reset_index(drop=True)

                earnings_dt = pd.to_datetime(earnings)
                exclude_dates = set()
                target_dates_list = target_df_tmp["date"].tolist()
                date_to_idx = {d: i for i, d in enumerate(target_dates_list)}

                for ed in earnings_dt:
                    if ed in date_to_idx:
                        idx = date_to_idx[ed]
                    else:
                        diffs = (target_df_tmp["date"] - ed).abs()
                        idx = diffs.idxmin()
                    low = max(0, idx - earnings_window)
                    high = min(len(target_df_tmp) - 1, idx + earnings_window)
                    for i in range(low, high + 1):
                        if i < len(target_dates_list):
                            exclude_dates.add(target_dates_list[i])

                signal_dates = signal_dates[~signal_dates.isin(exclude_dates)]

        if signal_dates.empty:
            results.append({
                "pattern_id": _pattern_id(condition_descriptions, target),
                "conditions": condition_descriptions,
                "primary_ticker": primary_ticker,
                "target_ticker": target,
                "sample_size": 0,
                "statistically_reliable": False,
                "message": "No matching signals (all excluded by earnings)",
            })
            continue

        # Compute forward returns (vectorized for speed)
        target_df = pd.DataFrame(target_rows)
        returns_df = compute_forward_returns_fast(target_df, signal_dates, horizons)

        if returns_df.empty:
            results.append({
                "pattern_id": _pattern_id(condition_descriptions, target),
                "conditions": condition_descriptions,
                "primary_ticker": primary_ticker,
                "target_ticker": target,
                "sample_size": 0,
                "statistically_reliable": False,
                "message": "No forward returns could be computed",
            })
            continue

        # Calculate statistics
        sample_size = len(returns_df)
        stats = {"sample_size": sample_size, "statistically_reliable": sample_size >= 30}

        for h in horizons:
            col = f"d{h}"
            vals = returns_df[col].dropna()
            if len(vals) > 0:
                stats[f"avg_return_d{h}"] = round(vals.mean(), 4)
                stats[f"median_return_d{h}"] = round(vals.median(), 4)
                stats[f"win_rate_d{h}"] = round((vals > 0).mean() * 100, 2)
                stats[f"std_d{h}"] = round(vals.std(), 4)
                if vals.std() > 0:
                    stats[f"sharpe_d{h}"] = round((vals.mean() / vals.std()) * np.sqrt(252 / h), 4)
                else:
                    stats[f"sharpe_d{h}"] = 0.0
            else:
                stats[f"avg_return_d{h}"] = None
                stats[f"median_return_d{h}"] = None
                stats[f"win_rate_d{h}"] = None

        pattern_id = _pattern_id(condition_descriptions, target)
        distribution = {}
        for h in horizons:
            col = f"d{h}"
            vals = returns_df[col].dropna().tolist()
            distribution[f"d{h}"] = [round(v, 4) for v in vals]

        result = {
            "pattern_id": pattern_id,
            "conditions": condition_descriptions,
            "primary_ticker": primary_ticker,
            "target_ticker": target,
            "exclude_earnings": exclude_earnings,
            **stats,
            "distribution": distribution,
            "signal_dates": [d.strftime("%Y-%m-%d") for d in returns_df["signal_date"]],
        }

        results.append(result)
        save_queue.append(result)

    # ── 7. Batch-save results ────────────────────────────────────────────
    if save_queue:
        try:
            client = get_client()
            rows = []
            for r in save_queue:
                rows.append({
                    "pattern_id": r["pattern_id"],
                    "conditions": json.dumps(r["conditions"]),
                    "ticker": r["target_ticker"],
                    "asset_class": "",
                    "sample_size": r["sample_size"],
                    "win_rate": r.get("win_rate_d5"),
                    "avg_return_d1": r.get("avg_return_d1"),
                    "avg_return_d2": r.get("avg_return_d2"),
                    "avg_return_d5": r.get("avg_return_d5"),
                    "avg_return_d10": r.get("avg_return_d10"),
                    "median_return": r.get("median_return_d5"),
                    "sharpe": r.get("sharpe_d5"),
                })
            # Batch upsert in chunks of 200
            for i in range(0, len(rows), 200):
                chunk = rows[i : i + 200]
                client.table("backtest_results").upsert(chunk, on_conflict="pattern_id").execute()
        except Exception as e:
            print(f"  Warning: Could not save backtest results: {e}")

    elapsed = time.time() - t0

    # Filter to only meaningful results (with actual signal data)
    meaningful = [r for r in results if r.get("sample_size", 0) > 0]
    print(f"  Universe scan complete: {len(valid_targets)} tickers processed, "
          f"{len(meaningful)} with signals, in {elapsed:.1f}s")

    # Sort by edge strength (d5 avg return)
    meaningful.sort(key=lambda x: abs(x.get("avg_return_d5") or 0), reverse=True)
    return meaningful
