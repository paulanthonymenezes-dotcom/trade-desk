// NQ Backtester — natural-language query endpoint (PART 2).
//
// Browser sends { query }. This runs a server-side Claude tool-use loop with the
// five backtest functions exposed as tools; each tool call proxies to the VPS
// engine (http://64.225.57.14:8080, the emastudy FastAPI). Returns a plain-English
// answer plus the raw tool calls/results so the UI can render the supporting
// numbers. Every query+result is logged to the Supabase `state` row
// id="backtest-history" (a capped JSON log — no DDL needed).
//
// Env (already configured in Vercel): ANTHROPIC_API_KEY, SUPABASE_URL,
// SUPABASE_SERVICE_KEY.

const BT_BASE = "http://64.225.57.14:8080";
const MODEL_DEFAULT = "claude-sonnet-5";
const MAX_TURNS = 6;
const HISTORY_CAP = 200;

const TOOLS = [
  {
    name: "compute_ema_distance",
    description:
      "Current close distance (%) to each EMA on a timeframe, plus the historical percentile (how much of history was MORE stretched than now). Use to answer 'how oversold/extended are we'.",
    endpoint: "/ema_distance",
    input_schema: {
      type: "object",
      properties: {
        timeframe: { type: "string", enum: ["daily", "4h"], default: "daily" },
        ema_periods: { type: "array", items: { type: "integer" }, default: [9, 20, 50] },
        as_of: { type: "string", description: "optional YYYY-MM-DD; default latest bar" },
      },
    },
  },
  {
    name: "reclaim_probability",
    description:
      "For historical bars that closed a given depth below an EMA, the probability price reclaims the EMA within 1/3/5/10 bars and median bars-to-reclaim. depth_bucket is [low_pct, high_pct] e.g. [-3.5,-2.5] for 2.5-3.5% below.",
    endpoint: "/reclaim",
    input_schema: {
      type: "object",
      properties: {
        ema_period: { type: "integer", default: 20 },
        timeframe: { type: "string", enum: ["daily", "4h"], default: "daily" },
        depth_bucket: { type: "array", items: { type: "number" }, default: [-3.0, -2.0] },
        horizons: { type: "array", items: { type: "integer" }, default: [1, 3, 5, 10] },
      },
    },
  },
  {
    name: "further_drawdown",
    description:
      "For the cohort of historical instances that reached at least current_depth below an EMA, the distribution of ADDITIONAL drawdown (%) before recovery and bars-to-recover. current_depth is negative, e.g. -2.98.",
    endpoint: "/further_drawdown",
    input_schema: {
      type: "object",
      properties: {
        ema_period: { type: "integer", default: 20 },
        timeframe: { type: "string", enum: ["daily", "4h"], default: "daily" },
        current_depth: { type: "number", default: -2.98 },
      },
      required: ["current_depth"],
    },
  },
  {
    name: "weekend_gap_analysis",
    description:
      "Friday close -> Sunday reopen -> Monday 09:30 distributions (gap %, overnight-low %, Monday-open %). Optional regime_filter restricts to similar setups, e.g. {\"fri_dist20_max\": -2.0} = Fridays closing >=2% below the daily 20-EMA.",
    endpoint: "/weekend_gap",
    input_schema: {
      type: "object",
      properties: {
        regime_filter: {
          type: "object",
          description: "keys like fri_dist20_max, fri_dist50_max, fri_dist20_min (percent). AND-ed.",
        },
      },
    },
  },
  {
    name: "bracket_simulator",
    description:
      "Bar-by-bar OCO simulation on 5-min data over a cohort. offsets are fractions: upper_limit_offset (+, take-profit), lower_stop_offset (-, stop trigger), lower_limit_offset (-, stop-limit floor). Reports fill rate, % via each leg, gap-through-no-fill %, and account $ P&L at `size` contracts. cohort_filter e.g. {\"type\":\"weekend\",\"fri_dist20_max\":-2.0}.",
    endpoint: "/bracket",
    input_schema: {
      type: "object",
      properties: {
        upper_limit_offset: { type: "number" },
        lower_stop_offset: { type: "number" },
        lower_limit_offset: { type: "number" },
        cohort_filter: { type: "object" },
        size: { type: "integer", default: 1 },
      },
      required: ["upper_limit_offset", "lower_stop_offset", "lower_limit_offset"],
    },
  },
];

const SYSTEM = `You are the NQ Backtester assistant for a personal futures trading desk.
You answer questions about NQ (Nasdaq-100 futures) mean-reversion, EMA distance, weekend gaps, and protective-order behavior by calling the provided backtest tools against the user's own validated historical data (2008-present, through 2026-07-17).

Rules:
- ALWAYS call a tool for any quantitative claim. Never invent numbers from memory.
- ALWAYS report the sample size (n) behind any probability or distribution.
- NQ is $20 per point per contract. When the user gives an account size or asks in dollars, translate moves to dollars at $20/pt.
- The current setup (latest bar) is ~2.98% below the daily 20-EMA. If the user says "here"/"now"/"current" without a number, use compute_ema_distance first to ground it, then feed that depth into other tools.
- Be concise and plain-spoken. Lead with the direct answer, then the supporting numbers. Note tail risk honestly; don't over-reassure.
- If a probability is based on a small n (<20), say so.`;

async function callTool(name, input) {
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return { error: `unknown tool ${name}` };
  const r = await fetch(BT_BASE + tool.endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input || {}),
  });
  const text = await r.text();
  try { return JSON.parse(text); } catch { return { error: "bad engine response", raw: text.slice(0, 500) }; }
}

async function logHistory(entry) {
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return;
  const h = { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" };
  const base = url.replace(/\/$/, "");
  try {
    const g = await fetch(`${base}/rest/v1/state?id=eq.backtest-history&select=data`, { headers: h });
    const rows = await g.json();
    let list = (rows && rows[0] && rows[0].data && rows[0].data.entries) || [];
    list.unshift(entry);
    list = list.slice(0, HISTORY_CAP);
    const body = JSON.stringify({ id: "backtest-history", data: { entries: list } });
    // upsert
    await fetch(`${base}/rest/v1/state?on_conflict=id`, {
      method: "POST",
      headers: { ...h, Prefer: "resolution=merge-duplicates,return=minimal" },
      body,
    });
  } catch (e) { /* logging is best-effort */ }
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return res.status(500).json({ error: "ANTHROPIC_API_KEY not set in Vercel." });

  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch { return res.status(400).json({ error: "Invalid JSON" }); } }
  const query = body && body.query;
  if (!query || typeof query !== "string") return res.status(400).json({ error: "Missing 'query'." });
  const model = (body && body.model) || MODEL_DEFAULT;

  const apiTools = TOOLS.map(({ name, description, input_schema }) => ({ name, description, input_schema }));
  const messages = [{ role: "user", content: query }];
  const steps = [];

  try {
    let final = "";
    for (let turn = 0; turn < MAX_TURNS; turn++) {
      const resp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-api-key": apiKey, "anthropic-version": "2023-06-01" },
        body: JSON.stringify({ model, max_tokens: 1500, system: SYSTEM, tools: apiTools, messages }),
      });
      const data = await resp.json();
      if (data.error) return res.status(502).json({ error: "Anthropic: " + (data.error.message || "unknown") });

      const toolUses = (data.content || []).filter((b) => b.type === "tool_use");
      const textOut = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
      if (textOut) final = textOut;

      if (data.stop_reason !== "tool_use" || toolUses.length === 0) break;

      messages.push({ role: "assistant", content: data.content });
      const results = [];
      for (const tu of toolUses) {
        const result = await callTool(tu.name, tu.input);
        steps.push({ tool: tu.name, input: tu.input, result });
        results.push({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify(result) });
      }
      messages.push({ role: "user", content: results });
    }

    const entry = { ts: new Date().toISOString(), query, answer: final, tools: steps.map((s) => s.tool) };
    await logHistory(entry);

    return res.status(200).json({ answer: final, steps });
  } catch (e) {
    return res.status(502).json({ error: "Query failed: " + (e && e.message ? e.message : String(e)) });
  }
}
