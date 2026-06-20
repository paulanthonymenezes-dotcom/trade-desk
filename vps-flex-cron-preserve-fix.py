"""
DROP-IN FIX for /opt/edge-scanner/flex_cron_sync.py  (hourly IBKR sync cron)
===========================================================================

PROBLEM
-------
The cron reconstructs all src=="ibkr" trades from a fresh Flex pull every hour,
then carries user/auto annotations from the OLD trade onto the matching NEW one.
That carry-over uses an ALLOWLIST of field names. The allowlist has gone stale:
it is missing the fields added to the dashboard since the cron was last touched.
On the next run, every field NOT in the allowlist is silently dropped, wiping:

    shortSymbol, longSymbol, exitTime, tradeTypeOverride,
    vixEntry, ivRankEntry, macroEventFlag,
    mae, mfe, _autoMAE, _autoMFE

These are exactly the fields the MAE/MFE analysis and the Databento backfill
depend on. An allowlist will keep going stale every time a new field is added.

THE FIX (recommended): switch from allowlist to DENYLIST.
The IBKR reconstruction authoritatively OWNS a known, fixed set of fields
(ticker, strikes, P&L, etc.). EVERYTHING ELSE on the trade is an annotation and
should survive untouched. Preserve-all-except-owned never goes stale again.

The authoritative source of truth for both lists is index.html:
  - reconstructFlexTrades()  -> mkTrade()      (the fields recon SETS)  ~line 11548
  - applyFlexReconstruction()                  (the carry-over logic)   ~line 11609

------------------------------------------------------------------------------
HOW TO APPLY
------------------------------------------------------------------------------
In flex_cron_sync.py, find the loop that walks the freshly reconstructed trades
and copies annotations from the matched old trade (it currently has a hand-listed
set of keys like t["journal"] = old.get("journal", []) ...). Replace that whole
per-field block with a single call:

    merge_preserving_annotations(old_ibkr_trades, recon_trades)

Then write recon_trades back to Supabase as the new src=="ibkr" set, exactly as
before. Manual (src != "ibkr") trades are untouched — keep your existing split.

If your script instead keeps a literal PRESERVE_KEYS list, you can just paste the
COMPLETE_ANNOTATION_KEYS set below over your current list as a stopgap — but the
denylist function is the real fix.
------------------------------------------------------------------------------
"""

# ── The fields the IBKR Flex reconstruction authoritatively produces. ────────
# These are always taken FRESH from the new reconstruction and must NOT be
# carried over from the old trade. Mirrors mkTrade() in index.html.
# `id` is handled specially (kept stable from the old trade by signature).
RECON_OWNED_FIELDS = {
    "ticker", "assetClass", "tradeType", "status",
    "entryDate", "entryTime", "expiry", "exitDate", "exitTime",
    "shortStrike", "longStrike", "contracts",
    "shortSymbol", "longSymbol",            # recon sets these now; preserved only as fallback
    "premiumCollected", "legs", "dteAtEntry",
    "src", "realizedPnl",
}

# ── Signature used to match an old trade to its freshly-reconstructed twin. ───
# IDENTICAL to sig() in applyFlexReconstruction() (index.html ~line 11610).
def _sig(t: dict) -> str:
    return "|".join(str(t.get(k, "")) for k in
                    ("ticker", "entryDate", "expiry", "shortStrike", "longStrike", "status"))


def merge_preserving_annotations(old_ibkr_trades: list[dict],
                                 recon_trades: list[dict]) -> list[dict]:
    """Carry every annotation from old IBKR trades onto the matching fresh ones.

    DENYLIST semantics: any key on the old trade that is NOT in
    RECON_OWNED_FIELDS is copied onto the new trade UNLESS the fresh
    reconstruction already set a truthy value for it. This means:
      * New annotation fields are preserved automatically (no more staleness).
      * Fresh recon values (P&L, strikes, status, ...) always win.
      * Stable `id` is kept from the old trade.
      * tradeTypeOverride, if present, re-applies to tradeType (matches the UI).

    Mutates and returns recon_trades.
    """
    anno_by_sig = {_sig(o): o for o in old_ibkr_trades}

    for t in recon_trades:
        old = anno_by_sig.get(_sig(t))
        if not old:
            continue  # genuinely new trade — nothing to carry

        for key, val in old.items():
            if key in RECON_OWNED_FIELDS:
                # shortSymbol/longSymbol/exitTime: keep old only if recon left it blank
                if key in ("shortSymbol", "longSymbol", "exitTime") and not t.get(key) and val:
                    t[key] = val
                continue
            # Annotation field. Preserve old value unless recon already set something truthy.
            # (recon initialises journal/thesis/etc. to empty, so old wins for those.)
            if not t.get(key):
                t[key] = val

        # Stable id + trade-type override behaviour, mirroring the UI.
        if old.get("id") is not None:
            t["id"] = old["id"]
        if t.get("tradeTypeOverride"):
            t["tradeType"] = t["tradeTypeOverride"]

    return recon_trades


# ── Stopgap allowlist (only if you can't switch to the denylist right now). ───
# The COMPLETE current annotation set. Paste over your existing PRESERVE_KEYS.
# NOTE: this still goes stale the next time a field is added — prefer the
# denylist function above.
COMPLETE_ANNOTATION_KEYS = (
    "journal", "screenshots", "thesis", "lessonLearned", "exitReason",
    "tpPrice", "slPrice", "entryMode", "source", "sourcePostUrl", "chartLink",
    "exitThesis", "thesisAccuracy", "sentiment", "strategyType", "ideaSource",
    "earningsFlag", "vixEntry", "ivRankEntry", "macroEventFlag",
    "mae", "mfe", "_autoMAE", "_autoMFE",
    "shortSymbol", "longSymbol", "exitTime", "tradeTypeOverride", "rolls",
)
