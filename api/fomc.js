// Vercel serverless: latest FOMC statement (+ the prior one for comparison),
// pulled free from the Federal Reserve's monetary-policy press-release RSS.
// The desk renders these and can hand them to /api/anthropic for a hawkish/dovish
// "AI read". Edge-cached 24h (a statement only changes every ~6 weeks).

const UA = { "User-Agent": "Mozilla/5.0 (compatible; TradingDesk/1.0)" };

function extractStatement(html) {
  // The press-release body sits under id="article"; take from there, strip tags.
  const i = html.indexOf('id="article"');
  // slice past the end of the opening tag so the "id=\"article\">" remnant is gone
  let body = i >= 0 ? html.slice(html.indexOf(">", i) + 1) : html;
  body = body.replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, "");
  let txt = body.replace(/<[^>]+>/g, " ");
  txt = txt
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;/g, "'")
    .replace(/&rsquo;/g, "'").replace(/&lsquo;/g, "'").replace(/&ldquo;/g, '"')
    .replace(/&rdquo;/g, '"').replace(/&quot;/g, '"').replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&#\d+;/g, " ");
  return txt.replace(/\s+/g, " ").trim().slice(0, 4500);
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  try {
    const rss = await (await fetch("https://www.federalreserve.gov/feeds/press_monetary.xml", { headers: UA })).text();
    const items = [...rss.matchAll(/<item>([\s\S]*?)<\/item>/g)].map(m => {
      const g = tag => { const x = m[1].match(new RegExp(`<${tag}>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?<\\/${tag}>`)); return x ? x[1].trim() : ""; };
      return { title: g("title"), link: g("link"), pubDate: g("pubDate") };
    });
    const stmts = items.filter(it => /issues FOMC statement/i.test(it.title));
    // The same meeting's projections (dot plot/SEP) page, if present, for context.
    const proj = items.find(it => /economic projections/i.test(it.title));
    if (!stmts.length) return res.status(200).json({ s: "error", errmsg: "no FOMC statement in feed" });

    const grab = async (url) => { try { return extractStatement(await (await fetch(url, { headers: UA })).text()); } catch { return ""; } };
    const latest = stmts[0], prior = stmts[1];
    const text = await grab(latest.link);
    const priorText = prior ? await grab(prior.link) : "";

    res.setHeader("Cache-Control", "s-maxage=86400, stale-while-revalidate=3600");
    return res.status(200).json({
      s: "ok",
      date: latest.pubDate, url: latest.link, text,
      priorDate: prior ? prior.pubDate : "", priorText,
      projectionsUrl: proj ? proj.link : "",
    });
  } catch (e) {
    return res.status(200).json({ s: "error", errmsg: String((e && e.message) || e) });
  }
}
