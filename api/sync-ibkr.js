// Overnight IBKR Flex auto-sync (Vercel serverless + cron).
//
// SAFE BY DEFAULT. This function NEVER writes to Supabase unless the env var
// SYNC_WRITE_ENABLED === "1". With it unset it dry-runs: fetch + reconstruct +
// return a summary, no write. That lets the nightly cron deploy completely inert
// until it's been verified and explicitly armed.
//
// Reconstruction logic (reconstructFlexTrades / applyFlexReconstruction /
// parseCSVLine) is COPIED VERBATIM from index.html so the cron and the desk's
// SYNC button behave identically. applyFlexReconstruction preserves _autoMAE/
// _autoMFE and other annotations by matching trades on a signature — see the
// date-match note in the verify endpoint below.
//
// Verify without IBKR or writes:
//   POST a Flex CSV as the request body to /api/sync-ibkr?dryRun=1
//   → returns the reconstructed trades + whether MAE would be preserved.
//
// Arm it (only after verification): set SYNC_WRITE_ENABLED=1 in Vercel env.

import { reconcile } from "./reconcile.js";

const SUPABASE_URL = "https://arjpswrirszerhpbojgs.supabase.co";
// Service-role key (bypasses RLS so we can read AND write the state row). MUST be
// set in Vercel env — without it the state read comes back empty (RLS), and the
// empty-state guard below aborts rather than risk duplicating your whole history.
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY || "";
const SB_HEADERS = { apikey: SUPABASE_KEY, Authorization: "Bearer " + SUPABASE_KEY };

// ──────────────────────────────────────────────────────────────────────────
// VERBATIM from index.html — do not diverge.
// ──────────────────────────────────────────────────────────────────────────
function parseCSVLine(line) {
  const fields = []; let cur = ""; let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') { inQuote = !inQuote; continue; }
    if (c === ',' && !inQuote) { fields.push(cur.trim()); cur = ""; continue; }
    cur += c;
  }
  fields.push(cur.trim());
  return fields;
}

function reconstructFlexTrades(csvText) {
  const allLines = csvText.split(/\r?\n/).filter(l => l.trim());
  if (allLines.length < 2) return [];
  const sectionStarts = [];
  const isHeaderLine = l => /[",]AssetClass[",]/i.test(l) && /[",]Symbol[",]/i.test(l) && /[",]Quantity[",]/i.test(l);
  allLines.forEach((l, i) => { if (isHeaderLine(l)) sectionStarts.push(i); });
  if (!sectionStarts.length) return [];

  const norm = s => s.toLowerCase().replace(/[\s_\/]/g, "");
  const F = x => { const v = parseFloat(x); return isNaN(v) ? 0 : v; };
  const today = parseInt(new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' }).replace(/-/g, ''));
  const fmtDate = s => (s && s.length >= 8) ? s.slice(0, 4) + "-" + s.slice(4, 6) + "-" + s.slice(6, 8) : (s || "");
  const hhmm = dt => (dt && dt.includes(";")) ? dt.split(";")[1].slice(0, 2) + ":" + dt.split(";")[1].slice(2, 4) : "";
  const futRoot = (sym, und) => { const b = (und || sym || "").toUpperCase(); const m = b.match(/^(MNQ|MES|NQ|ES|M2K|MYM|RTY|YM)/); return m ? "/" + m[1] : b; };
  const isOpt = c => c === "OPT" || c === "FOP";

  const rows = [];
  function parseSection(sectionLines, kind) {
    if (sectionLines.length < 2) return;
    const header = parseCSVLine(sectionLines[0]);
    const col = (names) => { for (const n of names) { const i = header.findIndex(h => norm(h) === norm(n)); if (i >= 0) return i; } return -1; };
    const iCat = col(["AssetClass", "AssetCategory"]), iSym = col(["Symbol"]), iUnd = col(["UnderlyingSymbol"]),
      iStrike = col(["Strike"]), iExp = col(["Expiry", "Expiration"]), iPC = col(["Put/Call", "PutCall", "Right"]),
      iQty = col(["Quantity"]), iProc = col(["Proceeds"]),
      iPnl = col(["FifoPnlRealized", "RealizedPnl", "RealizedPnL", "Realized P/L", "RealizedPL"]),
      iCode = col(["Open/CloseIndicator", "OpenCloseIndicator", "Code"]),
      iTDate = col(["TradeDate", "Date"]), iDT = col(["DateTime", "Date/Time"]),
      iTT = col(["TransactionType", "Transaction Type"]);
    if (iCat < 0) return;
    for (let i = 1; i < sectionLines.length; i++) {
      const f = parseCSVLine(sectionLines[i]); if (f.length < 5) continue;
      const cat = (iCat >= 0 ? f[iCat] : "").toUpperCase();
      let code, qty = F(iQty >= 0 ? f[iQty] : 0), proc = F(iProc >= 0 ? f[iProc] : 0), pnl = F(iPnl >= 0 ? f[iPnl] : 0);
      if (kind === "exp") {
        const tt = (iTT >= 0 ? f[iTT] : "").toLowerCase();
        if (tt && !/expir|assign|exerc/.test(tt)) continue;
        code = "C";
        if (cat === "STK") continue;
      } else {
        const codeRaw = iCode >= 0 ? f[iCode] : "";
        code = codeRaw.includes("O") ? "O" : (/C|Ep/.test(codeRaw) ? "C" : "");
      }
      const tdate = iTDate >= 0 ? f[iTDate] : "";
      const dt = kind === "exp" ? (tdate + ";235959") : (iDT >= 0 ? f[iDT] : tdate);
      rows.push({
        cat, sym: iSym >= 0 ? f[iSym] : "", und: iUnd >= 0 ? f[iUnd] : "", strike: iStrike >= 0 ? f[iStrike] : "",
        expiry: iExp >= 0 ? f[iExp] : "", pc: iPC >= 0 ? f[iPC] : "", qty, proc, pnl, code,
        tdate, dt,
      });
    }
  }

  const firstEnd = sectionStarts.length > 1 ? sectionStarts[1] : allLines.length;
  parseSection(allLines.slice(sectionStarts[0], firstEnd), "trades");
  rows.sort((a, b) => (a.dt || a.tdate).localeCompare(b.dt || b.tdate));

  const ikey = r => isOpt(r.cat) ? [r.und || r.sym, r.strike, r.expiry, r.pc, r.cat, r.sym].join("|") : [r.sym, r.cat].join("|");

  const state = {}, legtrips = [];
  for (const r of rows) {
    if (r.cat === "CASH") continue;
    const k = ikey(r); let st = state[k]; if (!st) { st = { net: 0, odt: null, odate: null, pnl: 0, oproc: 0, oqty: 0 }; state[k] = st; }
    if (st.net === 0) { st.odt = r.dt; st.odate = r.tdate; st.pnl = 0; st.oproc = 0; st.oqty = 0; }
    if (r.code === "O") { st.oproc += r.proc; st.oqty += Math.abs(r.qty); }
    st.net += r.qty; st.pnl += r.pnl;
    if (Math.abs(st.net) < 1e-6) {
      legtrips.push({ asset: r.cat, under: isOpt(r.cat) ? (r.und || r.sym) : r.sym, strike: isOpt(r.cat) ? r.strike : "",
        expiry: isOpt(r.cat) ? r.expiry : "", side: isOpt(r.cat) ? r.pc : "", sym: r.sym, odt: st.odt, odate: st.odate,
        exit: r.tdate, xdt: r.dt, pnl: Math.round(st.pnl * 100) / 100, oproc: Math.round(st.oproc), oqty: st.oqty, short: st.oproc > 0 });
      st.odt = null;
    }
  }

  const label = (legs, asset) => {
    if (asset === "STK") return legs[0].oproc < 0 ? "Stock Long" : "Stock Short";
    const puts = legs.filter(l => l.side === "P"), calls = legs.filter(l => l.side === "C");
    if (legs.length === 1) { const l = legs[0]; return (l.short ? "Short " : "Long ") + (l.side === "P" ? "Put" : "Call"); }
    if (legs.length === 2 && puts.length === 2) { const sh = puts.filter(l => l.short), lo = puts.filter(l => !l.short); if (sh.length && lo.length) return F(sh[0].strike) > F(lo[0].strike) ? "Bull Put Spread" : "Bear Put Spread"; }
    if (legs.length === 2 && calls.length === 2) { const sh = calls.filter(l => l.short), lo = calls.filter(l => !l.short); if (sh.length && lo.length) return F(sh[0].strike) < F(lo[0].strike) ? "Bear Call Spread" : "Bull Call Spread"; }
    if (legs.length === 2 && puts.length && calls.length) return "Strangle";
    if (legs.length === 4 && puts.length === 2 && calls.length === 2) return "Iron Condor";
    return "Multi-leg";
  };
  const mkTrade = (legs, asset, status) => {
    const shorts = legs.filter(l => l.short), longs = legs.filter(l => !l.short);
    const sStrike = shorts.length ? F(shorts[0].strike) : F(legs[0].strike), lStrike = longs.length ? F(longs[0].strike) : 0;
    const ac = asset === "STK" ? "Stock" : (asset === "FOP" ? "Futures Options" : "Options");
    const ticker = asset === "FOP" ? futRoot(legs[0].sym, legs[0].under) : legs[0].under;
    const prem = legs.reduce((s, l) => s + l.oproc, 0);
    const contracts = Math.max(...legs.map(l => l.oqty));
    const odts = legs.map(l => l.odt).filter(Boolean); const odt = odts.length ? odts.sort()[0] : "";
    const xdts = legs.map(l => l.xdt).filter(Boolean); const xdt = xdts.length ? xdts.sort().reverse()[0] : "";
    const entryISO = fmtDate(legs[0].odate); const expiryISO = fmtDate(legs[0].expiry);
    let dte = null;
    if (expiryISO && entryISO) {
      const dteMs = Date.parse(expiryISO + "T00:00:00") - Date.parse(entryISO + "T00:00:00");
      if (!isNaN(dteMs)) dte = Math.round(dteMs / 86400000);
    }
    const shortSymbol = shorts.length ? shorts[0].sym : (legs[0] ? legs[0].sym : null);
    const longSymbol = longs.length ? longs[0].sym : null;
    const t = { ticker, assetClass: ac, tradeType: label(legs, asset), status,
      entryDate: entryISO, entryTime: hhmm(odt), expiry: expiryISO,
      exitTime: hhmm(xdt),
      shortStrike: sStrike, longStrike: lStrike, contracts: Math.round(contracts),
      shortSymbol, longSymbol,
      premiumCollected: ac === "Stock" ? 0 : Math.abs(prem), legs: legs.length,
      dteAtEntry: dte,
      src: "ibkr", rolls: [], journal: [], screenshots: [], thesis: "", exitReason: "", lessonLearned: "", tpPrice: "", slPrice: "", chartLink: "" };
    if (status === "Closed") { t.exitDate = fmtDate(legs.map(l => l.exit).sort().reverse()[0]); t.realizedPnl = Math.round(legs.reduce((s, l) => s + l.pnl, 0) * 100) / 100; }
    else { t.exitDate = null; t.realizedPnl = null; }
    return t;
  };

  const trades = [];
  // Stocks: simple grouping by open-date (no spread/roll concept).
  const stkg = {};
  for (const t of legtrips.filter(t => t.asset === "STK")) { const g = [t.under, t.odate].join("|"); (stkg[g] = stkg[g] || []).push(t); }
  for (const g in stkg) trades.push(mkTrade(stkg[g], "STK", "Closed"));
  // Options: ROLL-AWARE. The user runs credit spreads and rolls the LONG leg down on
  // adverse moves. So a trade = one SHORT-leg round-trip + every LONG round-trip that
  // opens during the short's life and protects it (put long below the short / call
  // long above). This keeps rolled-down longs with their short (true net P&L from
  // FifoPnlRealized) and separates concurrent spreads by nearest protecting strike —
  // instead of lump-by-expiry-day (which merged unrelated trades).
  const byExp = {};
  for (const t of legtrips.filter(t => t.asset !== "STK")) { const k = [t.under, t.expiry, t.asset].join("|"); (byExp[k] = byExp[k] || []).push(t); }
  for (const k in byExp) {
    const grp = byExp[k];
    const shorts = grp.filter(t => t.short).sort((a, b) => (a.odt || "").localeCompare(b.odt || ""));
    const longs = grp.filter(t => !t.short);
    const assign = new Array(longs.length).fill(-1);
    longs.forEach((l, li) => {
      let best = -1, bestD = Infinity;
      shorts.forEach((s, si) => {
        if (!(s.odt <= l.odt && l.odt <= s.xdt)) return;
        const ss = F(s.strike), ls = F(l.strike);
        const protects = (l.side === "P" && ss > ls) || (l.side === "C" && ss < ls);
        if (protects && Math.abs(ss - ls) < bestD) { bestD = Math.abs(ss - ls); best = si; }
      });
      assign[li] = best;
    });
    shorts.forEach((s, si) => {
      const tlegs = [s, ...longs.filter((l, li) => assign[li] === si)];
      const tr = mkTrade(tlegs, s.asset, "Closed");
      tr.rollCount = Math.max(0, tlegs.filter(x => !x.short).length - 1);
      if (tr.rollCount) tr.tradeType += ` (rolled ×${tr.rollCount})`;
      trades.push(tr);
    });
    longs.forEach((l, li) => { if (assign[li] < 0) trades.push(mkTrade([l], l.asset, "Closed")); });
  }

  const st2 = {};
  for (const r of rows) {
    if (r.cat === "CASH") continue;
    const k = ikey(r); let st = st2[k]; if (!st) { st = { net: 0, odt: null, odate: null, oproc: 0, oqty: 0, sym: r.sym }; st2[k] = st; }
    if (st.net === 0) { st.odt = r.dt; st.odate = r.tdate; st.oproc = 0; st.oqty = 0; st.sym = r.sym; }
    if (r.code === "O") { st.oproc += r.proc; st.oqty += Math.abs(r.qty); }
    st.net += r.qty;
  }
  const opengrp = {};
  for (const k in st2) {
    const st = st2[k]; if (Math.abs(st.net) < 1e-6) continue;
    const parts = k.split("|"); const opt = parts.length > 2; const cat = opt ? parts[4] : parts[1];
    const expiry = opt ? parts[2] : ""; if (opt && expiry && parseInt(expiry) < today) continue;
    const und = parts[0]; const g = [und, expiry, cat].join("|");
    (opengrp[g] = opengrp[g] || []).push({ side: opt ? parts[3] : "", short: st.oproc > 0, strike: opt ? parts[1] : "", oqty: st.oqty, oproc: st.oproc, sym: st.sym, under: und, odt: st.odt, odate: st.odate, expiry: expiry });
  }
  for (const g in opengrp) { const cat = g.split("|")[2]; trades.push(mkTrade(opengrp[g], cat, "Open")); }
  return trades;
}

// APPEND-ONLY merge. This NEVER reads, moves, or removes an existing trade — it
// only adds reconstructed CLOSED trades that aren't already in the desk. So every
// existing trade (and its MAE/MFE/annotations/stats) is preserved untouched.
//
// "Already present" is matched on entryTime (HH:MM) + leg-symbol overlap — NOT
// entryDate. That's deliberate: IBKR's TradeDate disagrees with the true fill
// date for overnight trades, so a date-based match would (a) orphan MAE and
// (b) re-add the long wings of cross-expiry spreads as duplicate single legs.
// entryTime + the exact contract symbol are stable in both representations.
function applyFlexAppendOnly(recon, STATE) {
  const existing = STATE.trades || [];
  const legs = t => [t.shortSymbol, t.longSymbol].filter(Boolean);
  // Match on leg SYMBOL + DATE proximity, NOT entryTime. Restored trades had their
  // entryTime cleaned to the real morning fill, while a raw Flex reconstruction uses
  // the evening session time — so entryTime equality wrongly treats existing trades
  // as new and DUPLICATES them. Leg symbols (series+strike+expiry) are highly unique;
  // a ±1.5-day window absorbs the evening→next-session-day date shift.
  const dnear = (a, b) => {
    if (!a || !b) return false;
    const da = Date.parse(String(a).slice(0, 10) + "T00:00:00Z");
    const db = Date.parse(String(b).slice(0, 10) + "T00:00:00Z");
    return isFinite(da) && isFinite(db) && Math.abs(da - db) <= 36 * 3600 * 1000;
  };
  const isPresent = (r) => {
    const rl = legs(r);
    if (rl.length) {
      return existing.some(e => legs(e).some(s => rl.includes(s)) &&
        (dnear(e.entryDate, r.entryDate) || dnear(e.exitDate, r.exitDate)));
    }
    // no leg symbols (plain stock): match ticker + close date + ~same realized P&L
    return existing.some(e => e.ticker === r.ticker && dnear(e.exitDate, r.exitDate) &&
      Math.abs((+e.realizedPnl || 0) - (+r.realizedPnl || 0)) < 1);
  };
  // RECENCY GUARD: only append trades that closed recently. A genuinely new trade is
  // days old at most; an "old" reconstructed trade that isn't matched is a dedup miss
  // (e.g. a slightly different regrouping) that would DUPLICATE history. Block it.
  const staleCutoff = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
  let nid = Math.max(0, ...existing.map(t => t.id || 0)) + 1;
  let added = 0, skipped = 0, openSkipped = 0, staleSkipped = 0;
  const addedTrades = [];
  for (const r of recon) {
    if (r.status !== "Closed") { openSkipped++; continue; } // open positions sync when you open the desk
    if (r.exitDate && r.exitDate < staleCutoff) { staleSkipped++; continue; } // too old to be new
    if (isPresent(r)) { skipped++; continue; }
    r.id = nid++;
    addedTrades.push(r);
    added++;
  }
  STATE.trades = existing.concat(addedTrades);
  STATE.nextId = Math.max(0, ...STATE.trades.map(t => t.id || 0)) + 1;
  return {
    added, skipped, openSkipped, staleSkipped, existing: existing.length, total: STATE.trades.length,
    addedSample: addedTrades.slice(0, 10).map(t => ({ ticker: t.ticker, type: t.tradeType, entryDate: t.entryDate, entryTime: t.entryTime, exitDate: t.exitDate, short: t.shortSymbol, pnl: t.realizedPnl })),
  };
}

// ──────────────────────────────────────────────────────────────────────────
async function fetchFlexCSV(token, queryId) {
  const base = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService";
  const get = async (u) => (await fetch(u)).text();
  const reqResp = await get(`${base}.SendRequest?t=${token}&q=${queryId}&v=3`);
  const ref = (reqResp.match(/<ReferenceCode>(\d+)<\/ReferenceCode>/) || [])[1];
  if (!ref) {
    const em = (reqResp.match(/<ErrorMessage>([^<]+)/) || [])[1] || "no reference code";
    throw new Error("IBKR: " + em);
  }
  for (let i = 0; i < 6; i++) {
    await new Promise(r => setTimeout(r, 3000 + i * 1500));
    const resp = await get(`${base}.GetStatement?q=${ref}&t=${token}&v=3`);
    if (resp.includes("in progress") || (resp.includes("<Status>Warn</Status>") && resp.includes("try again"))) continue;
    return resp;
  }
  throw new Error("IBKR statement not ready after polling");
}

async function loadState() {
  const r = await fetch(SUPABASE_URL + "/rest/v1/state?id=eq.main&select=data", { headers: SB_HEADERS });
  const rows = await r.json();
  return (rows && rows[0] && rows[0].data) || { trades: [] };
}
async function saveState(state) {
  const r = await fetch(SUPABASE_URL + "/rest/v1/state", {
    method: "POST",
    headers: { ...SB_HEADERS, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify({ id: "main", data: state }),
  });
  if (!r.ok) throw new Error("Supabase write failed: " + r.status);
}

export default async function handler(req, res) {
  // Writes ONLY when explicitly armed. Any other invocation is a dry-run.
  const armed = process.env.SYNC_WRITE_ENABLED === "1";
  const dryRun = !armed || req.query.dryRun === "1";

  try {
    const state = await loadState();

    // CSV provided in the body → verify reconstruction without IBKR (always dry).
    let csv = null;
    if (req.method === "POST") csv = typeof req.body === "string" ? req.body : (req.body && req.body.csv) || null;

    if (!csv) {
      const token = state.ibkrToken, queryId = state.ibkrQueryId;
      if (!token || !queryId) return res.status(200).json({ s: "error", errmsg: "no IBKR token/queryId in Supabase state" });
      csv = await fetchFlexCSV(token, queryId);
    }

    const recon = reconstructFlexTrades(csv);
    if (!recon.length) return res.status(200).json({ s: "error", errmsg: "no trades reconstructed from Flex CSV" });

    // SAFETY GUARD: if we got an empty existing state but the reconstruction found
    // trades, the state read failed (no SUPABASE_SERVICE_KEY / RLS) — ABORT. A real
    // account always has prior trades; appending here would duplicate everything.
    if ((state.trades || []).length === 0 && recon.length > 0) {
      return res.status(200).json({ s: "error", errmsg: "refused: existing state read came back empty (set SUPABASE_SERVICE_KEY in Vercel env). Not appending, to avoid duplicating history." });
    }

    // APPEND-ONLY: only adds new closed trades; never touches existing ones.
    const summary = applyFlexAppendOnly(recon, state);

    // FLAG-ONLY reconciliation: desk vs broker, per underlying, cumulatively since
    // the go-live cutoff. Never modifies trades — only writes state.reconcileFlags
    // for the user to review. `since` defaults to a forward cutoff so frozen history
    // isn't re-flagged (override by setting state.reconcileSince).
    const since = state.reconcileSince || "2026-06-20";
    const rec = reconcile(csv, state, since);
    rec.generatedAt = new Date().toISOString().slice(0, 10);
    state.reconcileFlags = rec;
    summary.reconcile = { since, flaggedCount: rec.flaggedCount, flags: rec.flags.slice(0, 20) };

    if (dryRun) {
      return res.status(200).json({ s: "ok", mode: (req.method === "POST") ? "verify-csv (no write)" : "dry-run (no write)", ...summary });
    }

    // CIRCUIT BREAKER: a normal nightly sync appends a handful of new closed trades.
    // If it suddenly wants to add a large batch, the dedup match (entryTime + leg
    // symbol) has failed — e.g. the reconstruction's entryTime no longer lines up
    // with restored trades — and writing would DUPLICATE history. Refuse and flag,
    // never corrupt. (Override per-run with ?force=1 once a large batch is verified.)
    const MAX_APPEND_PER_RUN = 25;
    if (summary.added > MAX_APPEND_PER_RUN && req.query.force !== "1") {
      return res.status(200).json({ s: "error", mode: "refused (circuit breaker)",
        errmsg: `refused: would append ${summary.added} trades (> ${MAX_APPEND_PER_RUN}). Likely a dedup mismatch that would duplicate history — NOT writing. Run ?dryRun=1 to inspect; add ?force=1 only if the batch is genuinely all-new.`,
        ...summary });
    }

    await saveState(state);
    return res.status(200).json({ s: "ok", mode: "WROTE (append-only)", ...summary });
  } catch (e) {
    return res.status(200).json({ s: "error", errmsg: String((e && e.message) || e) });
  }
}
