// Flag-only reconciliation: desk realizedPnl vs broker FifoPnlRealized.
//
// Granularity = per underlying, NET CUMULATIVELY since a `since` cutoff. This is
// deliberately coarse and it's why it works: it is immune to (a) the evening-Globex
// date shift (desk dates a fill 06-04, broker's session TradeDate is 06-05) — a
// per-DAY diff cries wolf on these; cumulative cancels them — and (b) the desk's
// per-trade lumping, which moves P&L BETWEEN same-underlying trades but leaves the
// underlying total correct. Validated on real history: per-day flagged 36 day-buckets
// (mostly offsetting adjacent-day pairs); per-underlying-cumulative flagged only 4,
// each <$325 — the genuine residue. Since 2026-05-01: zero.
//
// NEVER modifies trades. Produces a flag list for the user to review. History is
// frozen, so callers pass `since` (the go-live cutoff) and only fills/exits on or
// after it are reconciled. A non-empty result means: "go look at <underlying>'s
// recent trades — desk and broker disagree by $X."

function root(sym) { return String(sym || "").toUpperCase().replace(/[FGHJKMNQUVXZ]\d$/, ""); }   // NQM6 -> NQ
function deskRoot(t) { return String(t || "").toUpperCase().replace(/^\//, ""); }                    // /NQ  -> NQ
function isoDate(d) { const s = String(d || "").replace(/-/g, ""); return s.length >= 8 ? `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}` : ""; }
function splitCsv(line) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) { if (c === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else { if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c; }
  }
  out.push(cur); return out;
}

export function reconcile(csvText, state, since, tol = 25) {
  const lines = csvText.split(/\r?\n/).filter(l => l.trim());
  const hdr = splitCsv(lines[0]); const ix = n => hdr.indexOf(n);
  const iAC = ix("AssetClass"), iUS = ix("UnderlyingSymbol"), iTD = ix("TradeDate"),
        iOC = ix("Open/CloseIndicator"), iPnl = ix("FifoPnlRealized");

  const broker = {}, brokerDay = {};   // root -> total ; "root|day" -> total
  for (let i = 1; i < lines.length; i++) {
    const f = splitCsv(lines[i]);
    if (!["OPT", "FOP", "STK"].includes(f[iAC]) || f[iOC] !== "C") continue;
    const day = isoDate(f[iTD]); if (!day || (since && day < since)) continue;
    const r = root(f[iUS]); const v = parseFloat(f[iPnl]) || 0;
    broker[r] = (broker[r] || 0) + v; brokerDay[r + "|" + day] = (brokerDay[r + "|" + day] || 0) + v;
  }

  const desk = {}, deskDay = {};
  for (const t of state.trades || []) {
    if (t.status === "Open" || t.realizedPnl == null) continue;
    const day = String(t.exitDate || "").slice(0, 10); if (!day || (since && day < since)) continue;
    const r = deskRoot(t.ticker); const v = parseFloat(t.realizedPnl) || 0;
    desk[r] = (desk[r] || 0) + v; deskDay[r + "|" + day] = (deskDay[r + "|" + day] || 0) + v;
  }

  const flags = [];
  for (const r of new Set([...Object.keys(broker), ...Object.keys(desk)])) {
    const b = broker[r] || 0, d = desk[r] || 0;
    if (Math.abs(d - b) <= tol) continue;
    // per-day breakdown (hint only) for this off underlying
    const days = [...new Set([...Object.keys(deskDay), ...Object.keys(brokerDay)]
      .filter(k => k.startsWith(r + "|")).map(k => k.split("|")[1]))].sort()
      .map(day => ({ day, desk: Math.round(deskDay[r + "|" + day] || 0), broker: Math.round(brokerDay[r + "|" + day] || 0) }))
      .filter(x => Math.abs(x.desk - x.broker) > tol);
    flags.push({
      underlying: r, desk: Math.round(d), broker: Math.round(b), diff: Math.round(d - b),
      kind: !(r in desk) ? "missing-on-desk" : !(r in broker) ? "missing-at-broker" : "amount-mismatch",
      days,
    });
  }
  flags.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
  return { generatedAt: null, since: since || "all", tol, flaggedCount: flags.length, flags };
}
