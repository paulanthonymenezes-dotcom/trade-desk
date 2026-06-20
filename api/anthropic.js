// Vercel serverless proxy to Anthropic's Messages API.
//
// Why: previously the desk hit https://api.anthropic.com/v1/messages directly
// from the browser with an x-api-key header pulled from localStorage. That
// requires the API key to live client-side — exposing it to anyone viewing
// the page source or network tab. With this proxy the key stays server-side
// (Vercel env var ANTHROPIC_API_KEY) and the browser only sees the proxy.
//
// Client usage:
//   fetch("/api/anthropic", { method:"POST", headers:{"Content-Type":"application/json"},
//                             body: JSON.stringify({ model, max_tokens, system, messages }) })
//
// The body shape matches Anthropic's /v1/messages exactly — pass through.
//
// Setup once in Vercel: Project → Settings → Environment Variables →
//   ANTHROPIC_API_KEY = sk-ant-api03-...
// Then redeploy (env var picks up on next build).

export default async function handler(req, res) {
  // CORS — we only call this from our own origin in normal use, but allow
  // dev/preview to work too.
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: "ANTHROPIC_API_KEY env var not set in Vercel. Project → Settings → Environment Variables.",
    });
  }

  // Parse body — Vercel auto-parses JSON when Content-Type is application/json,
  // but defensively fall back if it's still a string.
  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { return res.status(400).json({ error: "Invalid JSON body" }); }
  }
  if (!body || typeof body !== "object") return res.status(400).json({ error: "Missing JSON body" });

  // Minimal sanity-cap so a runaway prompt can't burn unlimited tokens.
  if (typeof body.max_tokens !== "number" || body.max_tokens > 4096) {
    body.max_tokens = Math.min(body.max_tokens || 2000, 4096);
  }

  try {
    const upstream = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify(body),
    });
    const text = await upstream.text();
    res.status(upstream.status).setHeader("Content-Type", "application/json").send(text);
  } catch (e) {
    res.status(502).json({ error: "Upstream error: " + (e && e.message ? e.message : String(e)) });
  }
}
