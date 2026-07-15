// Vercel serverless proxy — G7 macro economic calendar.
//
// Source: RapidAPI "Global Economic Calendar (Multi-Language)". Key stays
// server-side (RAPIDAPI_KEY env). The browser only ever hits /api/macro-calendar.
//
// Quota protection (2026-07-15): the RapidAPI plan is request-limited, and edge
// caching alone wasn't enough — every Vercel deploy busts the edge cache, and a
// day of frequent deploys re-hit RapidAPI on each cold call. So we now cache the
// mapped events in SUPABASE (state row id="macro-cache"), keyed by ET date. First
// call of the day fetches upstream once and stores it; every later call (any
// device, any deploy, any days= window) reads the cached set. Result: ~1 upstream
// RapidAPI call/day, period. Falls back to a live fetch if Supabase isn't reachable.
//
// The upstream API ignores its own country/date params, so we fetch a wide page,
// filter to G7 + non-LOW server-side, cache a 45-day window, and slice to the
// requested `days` per request. Returns:
//   { s: "ok", events: [{type, date, importance, actual, previous, estimate, unit, country}] }
//   { s: "error", errmsg }   ← UI degrades gracefully

const RAPIDAPI_HOST = "global-economic-calendar-api-multi-language.p.rapidapi.com";
const SUPABASE_URL = "https://arjpswrirszerhpbojgs.supabase.co";
const SB_KEY = process.env.SUPABASE_SERVICE_KEY || "";
const SB_HEADERS = { apikey: SB_KEY, Authorization: "Bearer " + SB_KEY };

async function readMacroCache() {
  if (!SB_KEY) return null;
  try {
    const r = await fetch(SUPABASE_URL + "/rest/v1/state?id=eq.macro-cache&select=data", { headers: SB_HEADERS });
    if (!r.ok) return null;
    const rows = await r.json();
    return (rows && rows[0] && rows[0].data) || null;
  } catch (e) { return null; }
}
async function writeMacroCache(obj) {
  if (!SB_KEY) return;
  try {
    await fetch(SUPABASE_URL + "/rest/v1/state", {
      method: "POST",
      headers: { ...SB_HEADERS, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify({ id: "macro-cache", data: obj }),
    });
  } catch (e) {}
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");

  const COUNTRIES = new Set(["US", "GB", "DE", "FR", "IT", "CA", "JP", "EU", "CN"]);
  const days = Math.max(1, Math.min(45, parseInt(req.query.days || "31", 10) || 31));
  const iso = d => d.toISOString().slice(0, 10);
  const now = new Date();
  const lo = iso(now);
  const hi = iso(new Date(now.getTime() + days * 86400000));
  const todayET = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });

  try {
    // 1) Try the durable Supabase cache first (today's ET date).
    let wide = null;
    const cache = await readMacroCache();
    if (cache && cache.date === todayET && Array.isArray(cache.events)) {
      wide = cache.events;
    } else {
      // 2) Cache miss → one upstream RapidAPI call, then store a 45-day window.
      const key = process.env.RAPIDAPI_KEY || "d625765864msh5f199ed93750b24p152611jsn83a16f689335";
      const r = await fetch(`https://${RAPIDAPI_HOST}/api/v1/economic-calendar/events?limit=1000`,
        { headers: { "x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": key } });
      if (r.status === 429) {
        // Quota hit — serve stale cache if we have any, else error.
        if (cache && Array.isArray(cache.events)) { wide = cache.events; }
        else { res.status(200).json({ s: "error", errmsg: "RapidAPI daily quota exceeded" }); return; }
      } else {
        const body = await r.json();
        const rows = Array.isArray(body) ? body : (body && body.data);
        if (!Array.isArray(rows)) {
          if (cache && Array.isArray(cache.events)) { wide = cache.events; }
          else { res.status(200).json({ s: "error", errmsg: (body && body.message) || "unexpected response shape" }); return; }
        } else {
          const maxHi = iso(new Date(now.getTime() + 45 * 86400000));
          wide = [];
          for (const e of rows) {
            if (!COUNTRIES.has(e.country_code)) continue;
            if (String(e.importance || "").toUpperCase() === "LOW") continue;
            const ts = e.occurrence_time || "";
            const day = ts.slice(0, 10);
            if (!day || day < lo || day > maxHi) continue;
            const loc = e.localization || {};
            wide.push({
              type: loc.long_name || loc.short_name || e.category || "Event",
              date: ts, importance: e.importance, actual: e.actual,
              previous: e.previous, estimate: e.forecast, unit: e.unit, country: e.country_code,
            });
          }
          wide.sort((a, b) => (a.date || "").localeCompare(b.date || ""));
          await writeMacroCache({ date: todayET, events: wide, fetchedAt: new Date().toISOString() });
        }
      }
    }

    // 3) Slice the cached wide window down to the requested days.
    const out = wide.filter(e => { const day = (e.date || "").slice(0, 10); return day && day >= lo && day <= hi; });
    res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate=3600");
    res.status(200).json({ s: "ok", events: out });
  } catch (e) {
    res.status(200).json({ s: "error", errmsg: String((e && e.message) || e) });
  }
}
