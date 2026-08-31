// Vercel serverless proxy — key global macro indicators from FRED.
//
// FRED sends no CORS header, so the browser can't hit it directly — it calls
// /api/macro instead. The FRED key stays server-side (FRED_API_KEY env, with a
// fallback). ~18 series are fetched once/day and cached in SUPABASE (state row
// id="macro-fred-cache", keyed by ET date); every later call (any device, any
// deploy) reads the cache. Edge-cached 24h on top. CPI/PPI index series are
// converted to YoY %; rates/levels are shown as-is. Each item carries its
// observation date so the UI can flag anything the free feeds report stale
// (Canada/Japan/China/UK CPI lag on free sources — US + Eurozone are current).
//
// Returns { s:"ok", groups:[{c, flag, items:[{label, value, unit, asOf}]}] }
//         { s:"error", errmsg }

const SUPABASE_URL = "https://arjpswrirszerhpbojgs.supabase.co";
const SB_KEY = process.env.SUPABASE_SERVICE_KEY || "";
const SB_HEADERS = { apikey: SB_KEY, Authorization: "Bearer " + SB_KEY };

// kind: "yoy" = index series → year-over-year %; "level" = show latest as-is.
// src:  "fred" (default) or "boc" (Bank of Canada Valet API — fresh Canadian
//       yields/CPI/mortgage rates that FRED's discontinued OECD feeds no longer carry).
const SERIES = [
  { c: "United States", flag: "🇺🇸", label: "CPI (YoY)",        id: "CPIAUCSL",           kind: "yoy",   unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "Core CPI (YoY)",   id: "CPILFESL",           kind: "yoy",   unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "PPI (YoY)",        id: "PPIFIS",             kind: "yoy",   unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "Fed Funds Rate",   id: "FEDFUNDS",           kind: "level", unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "2Y Treasury",      id: "DGS2",               kind: "level", unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "10Y Treasury",     id: "DGS10",              kind: "level", unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "20Y Treasury",     id: "DGS20",              kind: "level", unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "30Y Treasury",     id: "DGS30",              kind: "level", unit: "%" },
  { c: "United States", flag: "🇺🇸", label: "UMich Sentiment",  id: "UMCSENT",            kind: "level", unit: ""  },
  { c: "United States", flag: "🇺🇸", label: "Unemployment",     id: "UNRATE",             kind: "level", unit: "%" },
  { c: "Eurozone",      flag: "🇪🇺", label: "CPI (YoY)",        id: "CP0000EZ19M086NEST", kind: "yoy",   unit: "%" },
  { c: "Eurozone",      flag: "🇪🇺", label: "ECB Deposit Rate", id: "ECBDFR",             kind: "level", unit: "%" },
  // Canada — Bank of Canada Valet (daily, fresh). 5Y bond drives fixed-mortgage renewals;
  // overnight target drives variable/HELOC. Posted 5Y conventional shown for reference.
  { c: "Canada",        flag: "🇨🇦", label: "CPI (YoY)",        id: "STATIC_TOTALCPICHANGE", src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "Core CPI (trim)",  id: "CPI_TRIM",              src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "Overnight Rate",   id: "V39079",                src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "2Y Gov Bond",      id: "BD.CDN.2YR.DQ.YLD",     src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "5Y Gov Bond",      id: "BD.CDN.5YR.DQ.YLD",     src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "10Y Gov Bond",     id: "BD.CDN.10YR.DQ.YLD",    src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "30Y Gov Bond",     id: "BD.CDN.LONG.DQ.YLD",    src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "5Y Fixed Mortgage",id: "V80691311",             src: "boc", kind: "level", unit: "%" },
  { c: "Canada",        flag: "🇨🇦", label: "Unemployment",     id: "LRHUTTTTCAM156S",    kind: "level", unit: "%" },
  { c: "United Kingdom",flag: "🇬🇧", label: "CPI (YoY)",        id: "CPALTT01GBM659N",    kind: "level", unit: "%" },
  { c: "United Kingdom",flag: "🇬🇧", label: "Overnight Rate",   id: "IUDSOIA",            kind: "level", unit: "%" },
  { c: "Japan",         flag: "🇯🇵", label: "Policy Rate",      id: "IRSTCI01JPM156N",    kind: "level", unit: "%" },
  { c: "Japan",         flag: "🇯🇵", label: "Unemployment",     id: "LRHUTTTTJPM156S",    kind: "level", unit: "%" },
  { c: "China",         flag: "🇨🇳", label: "CPI (YoY)",        id: "CPALTT01CNM659N",    kind: "level", unit: "%" },
];
const ORDER = ["United States", "Eurozone", "Canada", "United Kingdom", "Japan", "China"];

async function readCache() {
  if (!SB_KEY) return null;
  try {
    const r = await fetch(SUPABASE_URL + "/rest/v1/state?id=eq.macro-fred-cache&select=data", { headers: SB_HEADERS });
    if (!r.ok) return null;
    const rows = await r.json();
    return (rows && rows[0] && rows[0].data) || null;
  } catch (e) { return null; }
}
async function writeCache(obj) {
  if (!SB_KEY) return;
  try {
    await fetch(SUPABASE_URL + "/rest/v1/state", {
      method: "POST",
      headers: { ...SB_HEADERS, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify({ id: "macro-fred-cache", data: obj }),
    });
  } catch (e) {}
}
// Bank of Canada Valet API — no key, ascending observations. Returns [{d, v}] asc.
async function bocObs(series, startDate) {
  const u = `https://www.bankofcanada.ca/valet/observations/${series}/json?start_date=${startDate}`;
  const r = await fetch(u);
  if (!r.ok) throw new Error("BoC " + r.status);
  const d = await r.json();
  const out = [];
  for (const o of (d.observations || [])) {
    const k = Object.keys(o).find(x => x !== "d");
    const v = o[k] && o[k].v;
    if (v != null && v !== "") out.push({ d: o.d, v: +v });
  }
  return out;
}
async function fredObs(id, key, limit = 14, freq = "") {
  let u = `https://api.stlouisfed.org/fred/series/observations?series_id=${id}&api_key=${key}&file_type=json&sort_order=desc&limit=${limit}`;
  if (freq) u += `&frequency=${freq}&aggregation_method=avg`;
  const r = await fetch(u);
  if (!r.ok) throw new Error("FRED " + r.status);
  const d = await r.json();
  return (d.observations || []).filter(o => o.value !== "." && o.value != null && o.value !== "");
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  const todayET = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  try {
    const cache = await readCache();
    if (cache && cache.date === todayET && Array.isArray(cache.groups)) {
      res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate=3600");
      res.status(200).json({ s: "ok", groups: cache.groups, fetchedAt: cache.fetchedAt, cached: true });
      return;
    }
    const key = process.env.FRED_API_KEY || "988b4dae25983b38e3f62a3e24c772af";
    const bocStart = new Date(Date.now() - 25 * 365 * 86400000).toISOString().slice(0, 10);  // ~25y of history
    const byCountry = {};
    for (const s of SERIES) {
      let value = null, asOf = null, hist = [];
      try {
        // oldest → newest. FRED monthly (up to ~54y); BoC Valet (daily, ~25y) for Canada.
        let asc;
        if (s.src === "boc") {
          asc = await bocObs(s.id, bocStart);
        } else {
          const obs = await fredObs(s.id, key, 650, "m");
          asc = obs.slice().reverse().map(o => ({ d: o.date, v: +o.value }));
        }
        let series;
        if (s.kind === "yoy") {
          series = [];
          for (let i = 12; i < asc.length; i++) {
            if (asc[i - 12].v) series.push({ d: asc[i].d, v: (asc[i].v / asc[i - 12].v - 1) * 100 });
          }
        } else {
          series = asc;
        }
        if (series.length) {
          value = series[series.length - 1].v;
          asOf = series[series.length - 1].d;
          const step = Math.max(1, Math.ceil(series.length / 150));   // downsample to ~150 pts
          hist = series.filter((_, i) => i % step === 0 || i === series.length - 1)
                       .map(p => ({ d: p.d, v: +p.v.toFixed(2) }));
        }
      } catch (e) {}
      (byCountry[s.c] = byCountry[s.c] || { c: s.c, flag: s.flag, items: [] })
        .items.push({ label: s.label, value: value != null ? +value.toFixed(2) : null, unit: s.unit, asOf, hist });
    }
    const groups = ORDER.filter(c => byCountry[c]).map(c => byCountry[c]);
    await writeCache({ date: todayET, groups, fetchedAt: new Date().toISOString() });
    res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate=3600");
    res.status(200).json({ s: "ok", groups, fetchedAt: new Date().toISOString() });
  } catch (e) {
    res.status(200).json({ s: "error", errmsg: String((e && e.message) || e) });
  }
}
