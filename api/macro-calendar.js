// Vercel serverless proxy — US macro economic calendar.
//
// Source: RapidAPI "Global Economic Calendar (Multi-Language)".
// Key stays server-side in the Vercel env var RAPIDAPI_KEY (Project → Settings →
// Environment Variables), same pattern as ANTHROPIC_API_KEY. The browser only
// ever hits /api/macro-calendar — the key is never exposed.
//
// Quota: the RapidAPI plan is request-limited, so the response is edge-cached for
// 24h via Cache-Control s-maxage. Vercel's CDN serves the cached payload to every
// visitor; the upstream RapidAPI call only re-runs ~once per day. Plenty fresh —
// the next 7 days of macro events don't move hour to hour.
//
// The upstream API ignores its own country/date query params, so we fetch a wide
// page and filter SERVER-SIDE: US only, [today, today+days], drop LOW-importance
// noise (bill auctions, CFTC positioning). Returns:
//   { s: "ok", events: [{type, date, importance, actual, previous, estimate, unit, country}] }
//   { s: "error", errmsg }   ← UI degrades gracefully on this

const RAPIDAPI_HOST = "global-economic-calendar-api-multi-language.p.rapidapi.com";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");

  // The user's NQ_Platform env var is a DIFFERENT RapidAPI key not subscribed to
  // this calendar API ("not subscribed" error). The key the user pasted (below) IS
  // subscribed and verified working. Use it; allow RAPIDAPI_KEY env to override.
  const key = process.env.RAPIDAPI_KEY || "d625765864msh5f199ed93750b24p152611jsn83a16f689335";

  // G7 + EU + China. The API ignores its own country/date params, so we filter
  // server-side. It also caps each page at 1000 results (~18 days of this set),
  // so we paginate via offset until the window is covered.
  const COUNTRIES = new Set(["US", "GB", "DE", "FR", "IT", "CA", "JP", "EU", "CN"]);
  const days = Math.max(1, Math.min(45, parseInt(req.query.days || "31", 10) || 31));
  const iso = d => d.toISOString().slice(0, 10);
  const now = new Date();
  const lo = iso(now);
  const hi = iso(new Date(now.getTime() + days * 86400000));

  try {
    // The API returns a fixed ~1000-event sample (offset is ignored), spanning
    // ~6 months — dense for the next few weeks. One fetch is all it gives.
    const r = await fetch(`https://${RAPIDAPI_HOST}/api/v1/economic-calendar/events?limit=1000`,
      { headers: { "x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": key } });
    if (r.status === 429) { res.status(200).json({ s: "error", errmsg: "RapidAPI daily quota exceeded" }); return; }
    const body = await r.json();
    const rows = Array.isArray(body) ? body : (body && body.data);
    if (!Array.isArray(rows)) { res.status(200).json({ s: "error", errmsg: (body && body.message) || "unexpected response shape" }); return; }

    const out = [];
    for (const e of rows) {
      if (!COUNTRIES.has(e.country_code)) continue;
      if (String(e.importance || "").toUpperCase() === "LOW") continue;
      const ts = e.occurrence_time || "";
      const day = ts.slice(0, 10);
      if (!day || day < lo || day > hi) continue;
      const loc = e.localization || {};
      out.push({
        type: loc.long_name || loc.short_name || e.category || "Event",
        date: ts,
        importance: e.importance,
        actual: e.actual,
        previous: e.previous,
        estimate: e.forecast,
        unit: e.unit,
        country: e.country_code,
      });
    }
    out.sort((a, b) => (a.date || "").localeCompare(b.date || ""));

    // Edge-cache 24h → ~1 upstream call/day regardless of traffic.
    res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate=3600");
    res.status(200).json({ s: "ok", events: out });
  } catch (e) {
    res.status(200).json({ s: "error", errmsg: String((e && e.message) || e) });
  }
}
