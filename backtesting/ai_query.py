from __future__ import annotations

"""AI-powered natural language → scanner query translator.

Uses the Anthropic API to interpret plain-English questions and convert
them into structured scan parameters for the pattern scanner.

Example questions:
  - "Which US equities go up in the week following a 0.25% FOMC rate cut?"
  - "What happens to gold when VIX is above 30?"
  - "Show me DXY behaviour after 3 consecutive down days in SPY"
"""
import json
from anthropic import Anthropic

from backtesting.config import ANTHROPIC_API_KEY
from backtesting.db import get_client, fetch_events, get_equity_universe
from backtesting.scanner.conditions import CONDITION_REGISTRY
from backtesting.scanner.pattern_scanner import scan_pattern, scan_universe

# ── System prompt describing the scanner capabilities ────────────────────────

_SYSTEM_PROMPT = """You are an AI assistant that translates natural-language
trading/backtesting questions into structured scan parameters.

You have access to a pattern scanner with these capabilities:

## Available Condition Types
{conditions_json}

## Available Event Types in the Database
- "rate_decision" — Central bank rate decisions (Fed/FOMC, ECB, BOE, BOJ, PBOC)
- "oil_shock" — Major oil price events
- "geopolitical" — Wars, elections, trade wars, sanctions
- "market_structure" — Crashes, flash crashes, circuit breakers
- "macro_surprise" — CPI/NFP/GDP surprises vs expectations

Events have fields: date, event_type, magnitude (float, e.g. 0.25 for a 25bp hike, -0.50 for a 50bp cut), geography (US/EU/UK/Japan/China), direction, description, tags.

## Scan Structure
A scan has:
- `primary_ticker`: The asset whose price history conditions are evaluated on (the SIGNAL source).
- `primary_conditions`: List of conditions applied to the primary ticker. Each is {{"type": "...", "params": {{...}}}}.
- `target_ticker`: The asset whose forward returns are measured (defaults to primary_ticker if null).
- `target_tickers`: For universe scans — a list of target tickers to measure forward returns across.
- `secondary_conditions`: Optional extra filters stacked with AND logic.
- `horizons`: Forward return periods in trading days [1, 2, 5, 10] by default.
- `exclude_earnings`: Whether to exclude ±3 trading days around target ticker earnings (default true).
- `event_filters`: Optional filters on events (source, actual_value ranges, etc.) to narrow event_trigger dates.

## Common Tickers
US Equities: AAPL, MSFT, NVDA, GOOG, AMZN, META, TSLA, JPM, BAC, GS, SPY, QQQ, IWM, XLF, XLE, etc.
FX: EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF, USD/CAD, etc.
Crypto: BTC/USD, ETH/USD, SOL/USD, etc.
Indices: DXY, VIX, SPX (use SPY as proxy), NDX (use QQQ), FTSE, DAX, NIKKEI, HSI, etc.
Commodities: GC (gold), CL (crude oil), SI (silver), NG (nat gas), HG (copper)

## IMPORTANT RULES
1. When the user asks about events (rate decisions, CPI, NFP, etc.), use the "event_trigger" condition with an `event_type` param.
2. When they want to filter events further (e.g., "rate cut of 0.25%"), include `event_filters` in your response.
3. When the user says "week" they typically mean 5 trading days (horizon d5).
4. For "which US equities..." questions, this is a UNIVERSE scan — set `scan_type` to "universe".
5. For cross-asset questions like "when X does Y, what happens to Z", X is primary_ticker and Z is target_ticker.
6. ALWAYS return valid JSON matching the schema below.

## Response Schema
Return ONLY a JSON object (no markdown, no explanation):
{{
  "scan_type": "single" | "universe",
  "interpretation": "Brief human-readable interpretation of what the scan does",
  "primary_ticker": "TICKER",
  "primary_conditions": [
    {{"type": "condition_type", "params": {{...}}}}
  ],
  "target_ticker": "TICKER or null for universe",
  "target_tickers": ["TICKER1", "TICKER2", ...],  // only for universe scans
  "secondary_conditions": [...] | null,
  "event_filters": {{
    "geography": "US" | "EU" | "UK" | "Japan" | "China" | null,
    "magnitude_min": number | null,   // e.g. 0.25 to get hikes >= 25bp
    "magnitude_max": number | null,   // e.g. 0.25 to get hikes <= 25bp
    "magnitude_abs_min": number | null,  // e.g. 0.25 for any change >= 25bp (up or down)
    "magnitude_abs_max": number | null,  // e.g. 0.25 for any change <= 25bp
    "description_contains": "string" | null  // keyword in description
  }} | null,
  "horizons": [1, 2, 5, 10],
  "exclude_earnings": true
}}
"""

# Dynamic universe — pull equity tickers from cached JSON (populated by seed)
_universe_cache = None

def _get_universe() -> list[str]:
    """Get the equity ticker universe for scanning.

    Uses the cached equity universe file (fast, <1ms) with a DB fallback.
    The cache is populated by seed scripts and stored at
    backtesting/data/equity_universe.json.
    """
    global _universe_cache
    if _universe_cache is not None:
        return _universe_cache

    tickers = get_equity_universe()
    if tickers:
        _universe_cache = tickers
    else:
        # Last-resort fallback if cache + DB both fail
        _universe_cache = [
            "AAPL", "ABBV", "ADBE", "AMD", "AMZN", "AVGO", "BA", "BAC", "C", "CAT",
            "COIN", "COP", "COST", "CRM", "CVX", "DIA", "DIS", "GE", "GLD", "GOOGL",
            "GS", "HD", "HON", "HYG", "INTC", "IWM", "JNJ", "JPM", "LCID", "LLY",
            "LQD", "MA", "MCD", "META", "MRK", "MS", "MSFT", "NFLX", "NKE", "NVDA",
            "ORCL", "PFE", "PLTR", "PYPL", "QCOM", "QQQ", "RIVN", "SBUX", "SLB",
            "SLV", "SOFI", "SPY", "SQ", "TGT", "TLT", "TSLA", "UNH", "UPS", "USO",
            "V", "VTI", "WFC", "WMT", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
            "XLP", "XLRE", "XLU", "XLV", "XOM",
        ]
    return _universe_cache

# ── Build conditions description for the prompt ─────────────────────────────

def _build_conditions_json() -> str:
    """Build a JSON description of available conditions for the system prompt."""
    out = {}
    for name, info in CONDITION_REGISTRY.items():
        out[name] = {
            "description": info["description"],
            "params": {k: v for k, v in info["params"].items() if k != "vix_df"},
        }
    return json.dumps(out, indent=2)


# ── Event date filtering ────────────────────────────────────────────────────

def _filter_event_dates(event_type: str, filters: dict | None = None) -> list[str]:
    """Fetch events and optionally filter by geography, magnitude, description."""
    events = fetch_events(event_type=event_type)
    if not events:
        return []

    filtered = events

    if filters:
        geo = filters.get("geography")
        if geo:
            filtered = [e for e in filtered if (e.get("geography") or "").upper() == geo.upper()]

        mag_min = filters.get("magnitude_min")
        if mag_min is not None:
            filtered = [e for e in filtered
                        if e.get("magnitude") is not None and float(e["magnitude"]) >= mag_min]

        mag_max = filters.get("magnitude_max")
        if mag_max is not None:
            filtered = [e for e in filtered
                        if e.get("magnitude") is not None and float(e["magnitude"]) <= mag_max]

        # Absolute magnitude filters (for "any change of X%" regardless of direction)
        abs_min = filters.get("magnitude_abs_min")
        if abs_min is not None:
            filtered = [e for e in filtered
                        if e.get("magnitude") is not None and abs(float(e["magnitude"])) >= abs_min]

        abs_max = filters.get("magnitude_abs_max")
        if abs_max is not None:
            filtered = [e for e in filtered
                        if e.get("magnitude") is not None and abs(float(e["magnitude"])) <= abs_max]

        desc_contains = filters.get("description_contains")
        if desc_contains:
            kw = desc_contains.lower()
            filtered = [e for e in filtered if kw in (e.get("description") or "").lower()]

    return [e["date"] for e in filtered]


# ── Main query function ─────────────────────────────────────────────────────

def ask_scanner(question: str) -> dict:
    """Translate a natural-language question into a scan and execute it.

    Returns:
        Dict with keys: interpretation, scan_params, results
    """
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not set in .env file"}

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Build system prompt with current condition registry
    system = _SYSTEM_PROMPT.format(conditions_json=_build_conditions_json())

    # Call Claude to translate the question
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": question}],
    )

    # Parse the response
    raw_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines)

    try:
        plan = json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "error": "AI could not parse your question into a valid scan. Try rephrasing.",
            "raw_response": raw_text,
        }

    interpretation = plan.get("interpretation", "")
    scan_type = plan.get("scan_type", "single")

    # ── Resolve event_trigger conditions with actual dates ────────────────
    for cond_list_key in ("primary_conditions", "secondary_conditions"):
        cond_list = plan.get(cond_list_key) or []
        for cond in cond_list:
            if cond["type"] == "event_trigger" and "event_type" in cond.get("params", {}):
                evt_type = cond["params"]["event_type"]
                dates = _filter_event_dates(evt_type, plan.get("event_filters"))
                cond["params"]["dates"] = dates
                # Remove event_type from params (the scanner expects "dates")
                cond["params"].pop("event_type", None)

    # ── Execute the scan ─────────────────────────────────────────────────
    primary_ticker = plan.get("primary_ticker", "SPY")
    primary_conditions = plan.get("primary_conditions", [])
    secondary_conditions = plan.get("secondary_conditions")
    horizons = plan.get("horizons", [1, 2, 5, 10])
    exclude_earnings = plan.get("exclude_earnings", True)

    if scan_type == "universe":
        target_tickers = plan.get("target_tickers") or _get_universe()
        results = scan_universe(
            primary_ticker=primary_ticker,
            primary_conditions=primary_conditions,
            target_tickers=target_tickers,
            secondary_conditions=secondary_conditions,
            exclude_earnings=exclude_earnings,
            horizons=horizons,
        )
        return {
            "interpretation": interpretation,
            "scan_type": "universe",
            "scan_params": {
                "primary_ticker": primary_ticker,
                "primary_conditions": primary_conditions,
                "target_tickers": target_tickers,
                "horizons": horizons,
            },
            "results": results,
        }
    else:
        target_ticker = plan.get("target_ticker") or primary_ticker
        result = scan_pattern(
            primary_ticker=primary_ticker,
            primary_conditions=primary_conditions,
            target_ticker=target_ticker,
            secondary_conditions=secondary_conditions,
            exclude_earnings=exclude_earnings,
            horizons=horizons,
        )
        return {
            "interpretation": interpretation,
            "scan_type": "single",
            "scan_params": {
                "primary_ticker": primary_ticker,
                "primary_conditions": primary_conditions,
                "target_ticker": target_ticker,
                "horizons": horizons,
            },
            "results": result,
        }


def explain_results(question: str, scan_results: dict) -> str:
    """Use Claude to generate a human-readable analysis of scan results.

    Takes the original question and raw scan results, returns a
    plain-English trading insight summary.
    """
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY not set."

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Trim distribution data to keep token count down
    results_copy = dict(scan_results)
    if "results" in results_copy:
        if isinstance(results_copy["results"], list):
            # Universe scan — trim each result
            trimmed = []
            for r in results_copy["results"][:20]:  # Top 20
                t = {k: v for k, v in r.items() if k != "distribution"}
                # Keep only first 5 signal dates
                if "signal_dates" in t:
                    t["signal_dates"] = t["signal_dates"][:5]
                trimmed.append(t)
            results_copy["results"] = trimmed
        elif isinstance(results_copy["results"], dict):
            r = results_copy["results"]
            r.pop("distribution", None)
            if "signal_dates" in r:
                r["signal_dates"] = r["signal_dates"][:10]

    prompt = f"""The user asked: "{question}"

Here are the backtest scan results:
{json.dumps(results_copy, indent=2, default=str)}

Provide a concise, actionable trading insight based on these results.
Include:
1. What the data shows (win rate, average return, sample size)
2. Whether the result is statistically reliable (sample size >= 30)
3. Any edge or bias detected (bullish/bearish/neutral)
4. Caveats (e.g., past performance, small sample, survivorship bias)

Keep it under 200 words. Use plain language a retail trader would understand.
Do NOT give investment advice — frame everything as historical observation."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()
