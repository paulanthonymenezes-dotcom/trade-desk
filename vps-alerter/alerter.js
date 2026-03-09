#!/usr/bin/env node
// ── Trading Desk Alert Server v2 ────────────────────────────────────────────
// Real-time spread monitoring + Telegram command center
// Runs on VPS 24/7, monitors via Supabase + MarketData.app + Yahoo Finance
// ─────────────────────────────────────────────────────────────────────────────

const cron = require('node-cron');

// ── Config ──────────────────────────────────────────────────────────────────
const SUPABASE_URL = "https://arjpswrirszerhpbojgs.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFyanBzd3JpcnN6ZXJocGJvamdzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzMzgyOTQsImV4cCI6MjA4NzkxNDI5NH0.aLCb5xP8WbeQuMpLJ3uoGFYebENCWQ-WBbtQZLvtYuA";

const MDA_TOKEN = "d2E2NDEybGtwZTBabnhSV2pkeEZBb3JfWW9uOHpKNnNIRTJ2bzNYZVlMcz0";
const MDA_BASE = "https://api.marketdata.app/v1";

const TG_BOT = "8441592699:AAE8T_GQhcPTrD7xT4PnKV6igMoTUxK6xRE";
const TG_CHAT = "6155190874";

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || "";

const SPREAD_CHECK_INTERVAL = 5 * 60 * 1000; // 5 min between spread refreshes
const STOCK_CHECK_INTERVAL = 60 * 1000;       // 60s between stock checks

// ── State ───────────────────────────────────────────────────────────────────
const alertsSent = new Set();
let lastMarketDate = null;
let lastState = null;
let consecutiveErrors = 0;
let spreadCache = {};        // { tradeId: { mid, high, low, delta, ts, fetchedAt } }
let prevSpreadCache = {};    // previous hour's spreads for change tracking
let prevQuoteCache = {};     // previous hour's stock prices for change tracking
let lastSpreadFetch = 0;
let tgOffset = 0;            // Telegram polling offset
let pendingTrade = null;     // Pending trade from screenshot, awaiting /yes confirmation
let pendingJournal = null;   // { date, timestamp } — pending EOD journal reply

// ── Sector lookup (mirrors index.html SECTOR_LOOKUP) ────────────────────────
const SECTOR_MAP = {
  AAPL:"Technology",MSFT:"Technology",NVDA:"Technology",AMD:"Technology",TSLA:"Technology",
  GOOGL:"Technology",GOOG:"Technology",META:"Technology",AMZN:"Consumer Discretionary",
  NFLX:"Communication Services",CRM:"Technology",ORCL:"Technology",ADBE:"Technology",
  QCOM:"Technology",INTC:"Technology",MU:"Technology",AVGO:"Technology",TSM:"Technology",
  JPM:"Financials",BAC:"Financials",GS:"Financials",MS:"Financials",C:"Financials",
  WFC:"Financials",V:"Financials",MA:"Financials",PYPL:"Financials",HOOD:"Financials",
  XOM:"Energy",CVX:"Energy",OXY:"Energy",USO:"ETF",
  JNJ:"Healthcare",PFE:"Healthcare",MRNA:"Healthcare",ABBV:"Healthcare",UNH:"Healthcare",
  KO:"Consumer Staples",PEP:"Consumer Staples",WMT:"Consumer Staples",COST:"Consumer Discretionary",
  NKE:"Consumer Discretionary",MCD:"Consumer Discretionary",
  CAT:"Industrials",BA:"Industrials",GE:"Industrials",
  PLTR:"Technology",YINN:"ETF",SPY:"ETF",QQQ:"ETF",VIX:"ETF",
};

function getSector(ticker, state) {
  return state?.sectors?.[ticker] || SECTOR_MAP[ticker] || "";
}

// ── Pre-trade checklist ─────────────────────────────────────────────────────
function runPreTradeChecklist(trade, state) {
  const flags = []; // { severity: 'warn'|'info', msg }
  const checks = []; // { pass: bool, msg }
  const open = (state.trades || []).filter(t => t.status === 'Open' && !(t.tradeType || '').startsWith('Stock'));
  const closed = (state.trades || []).filter(t => t.status !== 'Open');
  const acct = state.accountBalance || 0;
  const width = Math.abs(trade.shortStrike - trade.longStrike);
  const maxLoss = (width * 100 * trade.contracts) - trade.premiumCollected;
  const rr = trade.premiumCollected > 0 ? maxLoss / trade.premiumCollected : 999;

  // 1. Max loss vs account
  if (acct > 0) {
    const pct = (maxLoss / acct * 100);
    if (pct > 5) flags.push({ severity: 'warn', msg: `Max loss $${maxLoss.toFixed(0)} = ${pct.toFixed(1)}% of account` });
    else checks.push({ pass: true, msg: `Max loss ${pct.toFixed(1)}% of account` });
  }

  // 2. Sector concentration
  const sector = getSector(trade.ticker, state);
  if (sector) {
    const sameSector = open.filter(t => getSector(t.ticker, state) === sector);
    if (sameSector.length >= 2) flags.push({ severity: 'warn', msg: `${sameSector.length} open trades in ${sector} already` });
    else checks.push({ pass: true, msg: `${sector} — ${sameSector.length} existing` });
  }

  // 3. Duplicate ticker
  const dup = open.find(t => t.ticker === trade.ticker);
  if (dup) flags.push({ severity: 'warn', msg: `Already have open ${dup.tradeType} on ${trade.ticker}` });

  // 4. R:R ratio
  if (rr > 3) flags.push({ severity: 'warn', msg: `R:R ${rr.toFixed(1)}:1 (risking $${maxLoss.toFixed(0)} to make $${trade.premiumCollected})` });
  else checks.push({ pass: true, msg: `R:R ${rr.toFixed(1)}:1` });

  // 5. DTE
  const dte = trade.dteAtEntry || 0;
  if (dte <= 1) flags.push({ severity: 'info', msg: `0-1 DTE — aggressive expiry` });
  else if (dte > 45) flags.push({ severity: 'info', msg: `${dte} DTE — unusually long for credit spread` });
  else checks.push({ pass: true, msg: `${dte} DTE` });

  // 6. Position count
  if (open.length >= 5) flags.push({ severity: 'warn', msg: `${open.length} open positions — adding another` });
  else checks.push({ pass: true, msg: `${open.length} open positions` });

  // 7. Total portfolio risk
  if (acct > 0) {
    const existingRisk = open.reduce((s, t) => {
      const w = Math.abs((t.shortStrike || 0) - (t.longStrike || 0));
      return s + (w * (t.contracts || 1) * 100 - (t.premiumCollected || 0));
    }, 0);
    const totalRisk = existingRisk + maxLoss;
    const totalPct = (totalRisk / acct * 100);
    if (totalPct > 25) flags.push({ severity: 'warn', msg: `Total portfolio risk $${totalRisk.toFixed(0)} = ${totalPct.toFixed(0)}% of account` });
    else checks.push({ pass: true, msg: `Total risk ${totalPct.toFixed(0)}% of account` });
  }

  // 8. Historical ticker win rate
  const tickerTrades = closed.filter(t => t.ticker === trade.ticker);
  if (tickerTrades.length >= 3) {
    const wins = tickerTrades.filter(t => (t.realizedPnl || 0) > 0).length;
    const wr = Math.round(wins / tickerTrades.length * 100);
    if (wr < 50) flags.push({ severity: 'warn', msg: `${wr}% win rate on ${trade.ticker} (${wins}/${tickerTrades.length})` });
    else checks.push({ pass: true, msg: `${wr}% win rate on ${trade.ticker}` });
  }

  // 9. Wide spread
  if (width > 10) flags.push({ severity: 'info', msg: `$${width} wide spread` });

  return { flags, checks, maxLoss, rr, width };
}

function formatChecklist(ticker, trade, result) {
  const { flags, checks, maxLoss } = result;
  const width = Math.abs(trade.shortStrike - trade.longStrike);
  const perSpread = (trade.premiumCollected / (100 * trade.contracts)).toFixed(2);

  let msg = `📋 <b>PRE-TRADE CHECKLIST — ${ticker}</b>\n\n` +
    `${trade.tradeType}\n` +
    `${trade.shortStrike}/${trade.longStrike} x${trade.contracts}\n` +
    `Expiry: ${trade.expiry} (${trade.dteAtEntry} DTE)\n` +
    `Premium: $${trade.premiumCollected.toFixed(0)} ($${perSpread}/spread)\n` +
    `Max Loss: $${maxLoss.toFixed(0)}\n`;

  if (flags.length > 0) {
    msg += '\n';
    flags.forEach(f => {
      msg += f.severity === 'warn' ? `⚠️ ${f.msg}\n` : `ℹ️ ${f.msg}\n`;
    });
  }

  if (checks.length > 0) {
    msg += '\n';
    checks.forEach(c => { msg += `✅ ${c.msg}\n`; });
  }

  if (flags.length > 0) {
    msg += `\nReply /yes to confirm or /no to cancel`;
  }

  return msg;
}

// ── Telegram Send ───────────────────────────────────────────────────────────
async function tg(msg) {
  try {
    const r = await fetch(`https://api.telegram.org/bot${TG_BOT}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: TG_CHAT, text: msg, parse_mode: 'HTML' })
    });
    if (!r.ok) console.error('[TG] Send failed:', r.status);
  } catch (e) {
    console.error('[TG] Error:', e.message);
  }
}

// ── Supabase Load/Save ──────────────────────────────────────────────────────
async function loadState() {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/state?id=eq.main&select=data`, {
    headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` }
  });
  if (!r.ok) throw new Error(`Supabase GET ${r.status}: ${await r.text()}`);
  const rows = await r.json();
  if (!rows[0]?.data) throw new Error('No state found in Supabase');
  return rows[0].data;
}

async function saveState(state) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/state?id=eq.main`, {
    method: 'PATCH',
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': `Bearer ${SUPABASE_KEY}`,
      'Content-Type': 'application/json',
      'Prefer': 'return=minimal'
    },
    body: JSON.stringify({ data: state })
  });
  if (!r.ok) throw new Error(`Supabase PATCH ${r.status}: ${await r.text()}`);
}

// ── MarketData.app Fetch ────────────────────────────────────────────────────
async function mdaFetch(path, retries = 2) {
  const url = `${MDA_BASE}${path}${path.includes('?') ? '&' : '?'}token=${MDA_TOKEN}&format=json`;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const r = await fetch(url);
    if (r.ok) return r.json();
    if ((r.status === 429 || r.status === 403) && attempt < retries) {
      const wait = (attempt + 1) * 3000; // 3s, 6s
      console.warn(`[MDA] ${r.status} on attempt ${attempt + 1}, waiting ${wait/1000}s...`);
      await sleep(wait);
      continue;
    }
    throw new Error(`MDA ${r.status}`);
  }
}

async function fetchSpreadValue(trade) {
  if (!trade.expiry || !trade.shortStrike || !trade.longStrike) return null;

  const side = (trade.tradeType || '').toLowerCase().includes('put') ? 'put' : 'call';

  // Sequential fetches to avoid rate limiting
  const shortData = await mdaFetch(`/options/chain/${trade.ticker}/?expiration=${trade.expiry}&side=${side}&strike=${trade.shortStrike}`);
  await sleep(500);
  const longData = await mdaFetch(`/options/chain/${trade.ticker}/?expiration=${trade.expiry}&side=${side}&strike=${trade.longStrike}`);

  if (shortData.s !== 'ok' || longData.s !== 'ok') return null;

  const shortMid = shortData.mid?.[0];
  const longMid = longData.mid?.[0];
  if (shortMid == null || longMid == null) return null;

  const spreadMid = Math.abs(shortMid - longMid);
  const spreadHigh = Math.abs((shortData.ask?.[0] || shortMid) - (longData.bid?.[0] || longMid));
  const spreadLow = Math.abs((shortData.bid?.[0] || shortMid) - (longData.ask?.[0] || longMid));

  return {
    mid: spreadMid,
    high: Math.max(spreadHigh, spreadLow, spreadMid),
    low: Math.min(spreadHigh, spreadLow, spreadMid),
    shortMid, longMid,
    delta: shortData.delta?.[0] || null,
    iv: shortData.iv?.[0] || null,
    underlyingPrice: shortData.underlyingPrice?.[0] || null,
    ts: new Date().toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' }),
    fetchedAt: Date.now()
  };
}

async function refreshAllSpreads(trades) {
  const now = Date.now();
  if (now - lastSpreadFetch < SPREAD_CHECK_INTERVAL) return;
  lastSpreadFetch = now;

  const openTrades = trades.filter(t => t.status === 'Open');
  if (!openTrades.length) return;

  console.log(`[${ts()}] Refreshing spreads for ${openTrades.length} trades...`);

  for (const trade of openTrades) {
    try {
      const sv = await fetchSpreadValue(trade);
      if (sv) {
        spreadCache[trade.id] = sv;
        // Persist to trade object so dashboard has it on load
        trade._liveSpread = { mid: sv.mid, high: sv.high, low: sv.low, delta: sv.shortDelta, ts: sv.ts, _fetchedAt: Date.now() };
        console.log(`  ${trade.ticker} ${trade.shortStrike}/${trade.longStrike}: $${sv.mid.toFixed(2)} (${sv.ts})`);
      }
    } catch (e) {
      console.warn(`  ${trade.ticker}: ${e.message}`);
    }
    await sleep(1500); // rate limit buffer between trades
  }
}

// ── Yahoo Finance Quotes ────────────────────────────────────────────────────
async function fetchQuotes(tickers) {
  const results = {};
  await Promise.all(tickers.map(async (ticker) => {
    try {
      const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?interval=1d&range=1d&includePrePost=false`;
      const r = await fetch(url, {
        headers: { 'User-Agent': 'Mozilla/5.0 (compatible; TradingDesk/1.0)' }
      });
      if (!r.ok) return;
      const data = await r.json();
      const meta = data.chart?.result?.[0]?.meta;
      if (!meta?.regularMarketPrice) return;

      const prevClose = meta.chartPreviousClose || meta.previousClose;
      const price = meta.regularMarketPrice;
      results[ticker] = {
        price,
        change: price - prevClose,
        changePct: prevClose ? ((price - prevClose) / prevClose) * 100 : 0,
        volume: meta.regularMarketVolume || 0,
        dayHigh: meta.regularMarketDayHigh || null,
        dayLow: meta.regularMarketDayLow || null
      };
    } catch (e) {
      console.error(`[QUOTE] ${ticker}: ${e.message}`);
    }
  }));
  return results;
}

// ── P&L Calculations ────────────────────────────────────────────────────────
function intrinsicSpreadValue(trade, stockPrice) {
  if (!stockPrice || !trade.shortStrike || !trade.longStrike) return null;
  const width = Math.abs(trade.shortStrike - trade.longStrike);
  const type = (trade.tradeType || '').toLowerCase();
  if (type.includes('bear call') || type.includes('short call') || type.includes('covered call')) {
    return Math.max(0, Math.min(width, stockPrice - trade.shortStrike));
  }
  return Math.max(0, Math.min(width, trade.shortStrike - stockPrice));
}

function unrealizedPnl(trade, stockPrice) {
  const premium = trade.premiumCollected || 0;
  const contracts = trade.contracts || 1;

  // Prefer live spread data if fresh (< 10 min)
  const spread = spreadCache[trade.id];
  if (spread?.mid != null && spread?.fetchedAt) {
    const age = Date.now() - spread.fetchedAt;
    if (age < 10 * 60 * 1000) {
      return { pnl: premium - (spread.mid * 100 * contracts), source: 'live', spreadMid: spread.mid };
    }
  }

  // Fallback to intrinsic
  const itm = intrinsicSpreadValue(trade, stockPrice);
  if (itm === null) return null;
  return { pnl: premium - (itm * 100 * contracts), source: 'intrinsic', spreadMid: itm };
}

// ── Alert Checking ──────────────────────────────────────────────────────────
function checkAlerts(state, quotes) {
  const trades = (state.trades || []).filter(t => t.status === 'Open');
  const strikeProx = state.alertStrikeProx || 5;
  let alertCount = 0;

  for (const t of trades) {
    const q = quotes[t.ticker];
    if (!q) continue;

    const prox = ((q.price - t.shortStrike) / q.price) * 100;
    const dl = daysLeft(t.expiry);
    const key = t.id + '-';
    const spread = spreadCache[t.id];
    const pnlData = unrealizedPnl(t, q.price);

    // ── Stock price alerts ──────────────────────────────────────────────

    // Strike proximity warning
    if (prox < strikeProx && prox >= 0 && !alertsSent.has(key + 'prox')) {
      alertsSent.add(key + 'prox');
      const spreadLine = spread ? `\nSpread: $${spread.mid.toFixed(2)}` : '';
      tg(`⚠️ <b>${t.ticker}</b> — $${q.price.toFixed(2)} only ${prox.toFixed(1)}% above short strike $${t.shortStrike}\nDTE: ${dl}${spreadLine}`);
      alertCount++;
    }

    // Short strike breach
    if (q.price < t.shortStrike && !alertsSent.has(key + 'breach')) {
      alertsSent.add(key + 'breach');
      const spreadLine = spread ? `\nSpread: $${spread.mid.toFixed(2)}` : '';
      tg(`🔴 <b>${t.ticker} BREACHED SHORT STRIKE</b>\n$${q.price.toFixed(2)} < $${t.shortStrike} | DTE: ${dl}${spreadLine}`);
      alertCount++;
    }

    // TP hit (stock price)
    if (t.tpPrice && q.price >= parseFloat(t.tpPrice) && !alertsSent.has(key + 'tp')) {
      alertsSent.add(key + 'tp');
      tg(`✅ <b>${t.ticker} TAKE PROFIT HIT</b>\n$${q.price.toFixed(2)} ≥ TP $${t.tpPrice}\n${t.longStrike}/${t.shortStrike} x${t.contracts}`);
      alertCount++;
    }

    // SL hit (stock price — thesis broken)
    if (t.slPrice && q.price <= parseFloat(t.slPrice) && !alertsSent.has(key + 'sl')) {
      alertsSent.add(key + 'sl');
      const spreadLine = spread ? `\nSpread: $${spread.mid.toFixed(2)}` : '';
      tg(
        `🛑 <b>${t.ticker} PRICE STOP LOSS HIT</b>\n` +
        `$${q.price.toFixed(2)} ≤ SL $${t.slPrice} — thesis broken\n` +
        `${t.longStrike}/${t.shortStrike} x${t.contracts} | DTE: ${dl}${spreadLine}\n` +
        `<i>Close the trade — your thesis is invalidated</i>`
      );
      alertCount++;
    }

    // ── Spread alerts: skip if market hasn't settled or bid/ask too wide ──
    const etNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const etMins = etNow.getHours() * 60 + etNow.getMinutes();
    const marketSettled = etMins >= 575; // 9:35 AM ET — 5 min after open
    const spreadReliable = spread?.mid != null && spread.high != null && spread.low != null
      && (spread.high - spread.low) / spread.mid < 0.40; // bid/ask width < 40% of mid

    // ── Spread SL early warning (heads-up before hard stop) ─────────────
    // For credit spreads: SL triggers when spread DECAYS DOWN to the SL level
    // (spread getting cheaper = you can buy back near your SL price)
    if (t.spreadSL && marketSettled && spreadReliable && !alertsSent.has(key + 'spreadSLwarn')) {
      const ssl = parseFloat(t.spreadSL);
      const warnLevel = ssl * 1.15; // 15% above SL = early warning
      if (spread.mid <= warnLevel && spread.mid > ssl) {
        alertsSent.add(key + 'spreadSLwarn');
        const pctAway = ((spread.mid - ssl) / ssl * 100).toFixed(0);
        const premium = t.premiumCollected || 0;
        const contracts = t.contracts || 1;
        const costToClose = spread.mid * 100 * contracts;
        const pnl = premium - costToClose;
        tg(
          `⚠️ <b>${t.ticker} — APPROACHING SPREAD SL</b>\n\n` +
          `Spread: $${spread.mid.toFixed(2)} — ${pctAway}% from SL ($${t.spreadSL})\n` +
          `P&L: ${pnl >= 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(0)}\n` +
          `${t.shortStrike}/${t.longStrike} x${contracts} | DTE: ${dl}\n\n` +
          `<i>Watch closely. Hard stop at $${t.spreadSL}</i>`
        );
        alertCount++;
      }
    }

    // ── Spread stop loss (911 — non-negotiable) ─────────────────────────
    // Triggers when spread mid drops TO or BELOW the SL level

    if (t.spreadSL && marketSettled && spreadReliable && spread.mid <= parseFloat(t.spreadSL) && !alertsSent.has(key + 'spreadSL')) {
      alertsSent.add(key + 'spreadSL');
      const premium = t.premiumCollected || 0;
      const contracts = t.contracts || 1;
      const costToClose = spread.mid * 100 * contracts;
      const pnl = premium - costToClose;
      tg(
        `🚨🚨🚨 <b>${t.ticker} SPREAD STOP LOSS — GET OUT NOW</b>\n\n` +
        `Spread: $${spread.mid.toFixed(2)} ≤ SSL $${t.spreadSL}\n` +
        `P&L: ${pnl >= 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(0)}\n` +
        `${t.shortStrike}/${t.longStrike} x${contracts} | DTE: ${dl}\n\n` +
        `<b>THIS IS YOUR HARD STOP. CLOSE IT.</b>`
      );
      alertCount++;
    }

    // ── Percentage-based spread alerts (skip if market not settled) ─────

    if (pnlData && t.premiumCollected && marketSettled) {
      const premium = t.premiumCollected;
      const lossPct = pnlData.pnl < 0 ? Math.abs(pnlData.pnl) / premium * 100 : 0;
      const width = Math.abs(t.shortStrike - t.longStrike);
      const maxLoss = (width * 100 * (t.contracts || 1)) - premium;

      // 75% of premium lost
      if (lossPct >= 75 && !alertsSent.has(key + 'loss75')) {
        alertsSent.add(key + 'loss75');
        tg(
          `⚠️ <b>${t.ticker} — 75% PREMIUM LOSS</b>\n` +
          `Spread: $${pnlData.spreadMid.toFixed(2)} | P&L: -$${Math.abs(pnlData.pnl).toFixed(0)}\n` +
          `${t.shortStrike}/${t.longStrike} x${t.contracts} | DTE: ${dl}\n` +
          `<i>Source: ${pnlData.source}</i>`
        );
        alertCount++;
      }

      // 100% of premium lost (breakeven breached)
      if (lossPct >= 100 && !alertsSent.has(key + 'loss100')) {
        alertsSent.add(key + 'loss100');
        tg(
          `🔴 <b>${t.ticker} — BREAKEVEN BREACHED</b>\n` +
          `Spread: $${pnlData.spreadMid.toFixed(2)} | P&L: -$${Math.abs(pnlData.pnl).toFixed(0)}\n` +
          `Premium was $${premium.toFixed(0)} — now underwater\n` +
          `${t.shortStrike}/${t.longStrike} x${t.contracts} | DTE: ${dl}\n` +
          `<i>Source: ${pnlData.source}</i>`
        );
        alertCount++;
      }

      // 150% of premium lost
      if (lossPct >= 150 && !alertsSent.has(key + 'loss150')) {
        alertsSent.add(key + 'loss150');
        tg(
          `🚨 <b>${t.ticker} — 150% LOSS</b>\n` +
          `Spread: $${pnlData.spreadMid.toFixed(2)} | P&L: -$${Math.abs(pnlData.pnl).toFixed(0)}\n` +
          `Max loss: $${maxLoss.toFixed(0)} (${(Math.abs(pnlData.pnl) / maxLoss * 100).toFixed(0)}% of max)\n` +
          `${t.shortStrike}/${t.longStrike} x${t.contracts} | DTE: ${dl}\n` +
          `<i>Source: ${pnlData.source}</i>`
        );
        alertCount++;
      }

      // Spread value TP — if spread dropped to 50% of entry (e.g. collected $1.00, spread now $0.50)
      const spreadAtEntry = premium / (100 * (t.contracts || 1));
      if (pnlData.spreadMid <= spreadAtEntry * 0.5 && pnlData.spreadMid > 0 && !alertsSent.has(key + 'spread50')) {
        alertsSent.add(key + 'spread50');
        tg(
          `💰 <b>${t.ticker} — SPREAD AT 50% PROFIT</b>\n` +
          `Spread: $${pnlData.spreadMid.toFixed(2)} (entry: $${spreadAtEntry.toFixed(2)})\n` +
          `P&L: +$${pnlData.pnl.toFixed(0)} | ${t.shortStrike}/${t.longStrike} x${t.contracts}\n` +
          `<i>Consider closing for profit</i>`
        );
        alertCount++;
      }
    }

    // Expiry warning (1 DTE)
    if (dl === 1 && !alertsSent.has(key + 'exp')) {
      alertsSent.add(key + 'exp');
      const spreadLine = spread ? `Spread: $${spread.mid.toFixed(2)} | ` : '';
      tg(`⏰ <b>${t.ticker}</b> expires TOMORROW\n${spreadLine}${t.longStrike}/${t.shortStrike} x${t.contracts}`);
      alertCount++;
    }
  }

  return alertCount;
}

// ── Telegram Command Listener ───────────────────────────────────────────────
async function pollTelegram() {
  try {
    const r = await fetch(
      `https://api.telegram.org/bot${TG_BOT}/getUpdates?offset=${tgOffset}&timeout=30&allowed_updates=["message"]`,
      { signal: AbortSignal.timeout(35000) }
    );
    if (!r.ok) return;
    const data = await r.json();
    if (!data.ok || !data.result?.length) return;

    for (const update of data.result) {
      tgOffset = update.update_id + 1;
      const msg = update.message;
      if (!msg || String(msg.chat?.id) !== TG_CHAT) continue;

      // Handle photo messages (screenshot-to-trade)
      if (msg.photo && msg.photo.length > 0) {
        await handleScreenshot(msg);
        continue;
      }

      if (!msg.text) continue;
      const text = msg.text.trim();

      // Handle premium reply for pending screenshot trade
      if (pendingTrade?.needsPremium && /^\d+(\.\d+)?$/.test(text)) {
        const premium = parseFloat(text);
        pendingTrade.premium = premium;
        pendingTrade.needsPremium = false;
        const width = Math.abs(pendingTrade.shortStrike - pendingTrade.longStrike);
        const maxLoss = (width * 100 * pendingTrade.contracts) - premium;
        const perSpread = (premium / (100 * pendingTrade.contracts)).toFixed(2);
        tg(
          `📋 <b>Updated — ${pendingTrade.ticker}</b>\n\n` +
          `${pendingTrade.shortStrike}/${pendingTrade.longStrike} x${pendingTrade.contracts}\n` +
          `Premium: $${premium.toFixed(0)} ($${perSpread}/spread)\n` +
          `Max loss: $${maxLoss.toFixed(0)}\n\n` +
          `<b>Open this trade?</b>\n/yes — confirm\n/no — cancel`
        );
        continue;
      }

      if (text.startsWith('/')) {
        await handleCommand(text);
      } else if (pendingJournal && (Date.now() - pendingJournal.timestamp < 7200000)) {
        // Free-text reply within 2 hours of EOD prompt → save as journal entry
        try {
          const state = await loadState();
          if (!state.dailyJournal) state.dailyJournal = {};
          if (!state.dailyJournal[pendingJournal.date]) state.dailyJournal[pendingJournal.date] = {};
          const entry = state.dailyJournal[pendingJournal.date];
          // Append if multiple messages
          entry.reflection = entry.reflection ? entry.reflection + '\n\n' + text : text;
          entry.timestamp = new Date().toISOString();
          await saveState(state);
          const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
          const d = new Date(pendingJournal.date + 'T12:00:00');
          await tg(`✅ Journal saved for ${monthNames[d.getMonth()]} ${d.getDate()}. Nice work today.`);
          pendingJournal = null;
        } catch (e) {
          console.error('[JOURNAL-SAVE]', e.message);
          await tg(`Error saving journal: ${e.message}`);
        }
      }
    }
  } catch (e) {
    if (!e.message?.includes('abort')) {
      console.error('[TG-POLL]', e.message);
    }
  }
}

async function handleCommand(text) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase().replace('@pauls_trading_alerts_bot', '');

  try {
    switch (cmd) {
      case '/status': return await cmdStatus();
      case '/spreads': return await cmdSpreads();
      case '/pnl': return await cmdPnl();
      case '/sl': return await cmdUpdateField(parts, 'slPrice', 'Price Stop Loss');
      case '/ssl': return await cmdSpreadLevel(parts, 'sl');
      case '/stp': return await cmdSpreadLevel(parts, 'tp');
      case '/tp': return await cmdUpdateField(parts, 'tpPrice', 'Take Profit');
      case '/open': return await cmdOpen(parts);
      case '/close': return await cmdClose(parts);
      case '/yes': return await cmdConfirmTrade(true, parts.slice(1));
      case '/no': return await cmdConfirmTrade(false);
      case '/brief': return await sendDailyBrief();
      case '/journal': return await sendEODPrompt();
      case '/sync': return await autoSyncIBKR();
      case '/schedule': return await cmdSchedule();
      case '/help': return await cmdHelp();
      default: {
        // Check if it's a ticker command like /nvda /cat etc
        const ticker = cmd.replace('/', '').toUpperCase();
        if (ticker.length >= 1 && ticker.length <= 5 && /^[A-Z]+$/.test(ticker)) {
          return await cmdTicker(ticker);
        }
        tg(`Unknown command: ${cmd}\nType /help for available commands`);
      }
    }
  } catch (e) {
    console.error(`[CMD] ${cmd}: ${e.message}`);
    tg(`Error: ${e.message}`);
  }
}

async function cmdHelp() {
  tg(
    `<b>Trading Desk Commands</b>\n\n` +
    `<b>Positions</b>\n` +
    `/open TICKER SHORT/LONGP EXPIRY QTY PREMIUM\n` +
    `/close TICKER [PNL]\n` +
    `/status — All positions overview\n` +
    `/spreads — Live spread values\n` +
    `/pnl — P&L summary\n` +
    `/{ticker} — Detail on one position\n\n` +
    `<b>Risk Management</b>\n` +
    `/sl TICKER PRICE — Chart stop loss\n` +
    `/ssl TICKER %  — Spread stop loss (e.g. /ssl NVDA 75)\n` +
    `/stp TICKER %  — Spread take profit (e.g. /stp NVDA 50)\n` +
    `/tp TICKER PRICE — Chart take profit\n\n` +
    `<b>Daily</b>\n` +
    `/brief — Morning daily brief\n` +
    `/journal — Post-session journal prompt\n` +
    `/sync — IBKR Flex Query sync now\n` +
    `/schedule — Show automated message schedule\n\n` +
    `<b>Screenshot</b>\n` +
    `📸 Send a screenshot → auto-parse → /yes to confirm\n\n` +
    `<b>Examples</b>\n` +
    `<i>/open NFLX 950/940P 3/7 5 2500</i>\n` +
    `<i>/open AAPL 220/215P 0dte 3 900</i>\n` +
    `<i>/open TSLA 280/270P fri 2 1200</i>\n` +
    `<i>/close NFLX 1200</i>\n` +
    `<i>/sl NVDA 192 — thesis broken</i>\n` +
    `<i>/ssl NVDA 4.50 — hard stop (911)</i>`
  );
}

async function cmdSchedule() {
  tg(
    `<b>📅 Automated Schedule (Weekdays)</b>\n\n` +
    `<b>7:00 AM</b> — ☀️ Daily Brief\n` +
    `  Positions, account, risk, watchlist targets, flags\n\n` +
    `<b>9:30 AM</b> — 🟢 Market Open\n` +
    `  Position count, SL/TP status, alert server active\n\n` +
    `<b>10 AM–3 PM</b> — 🕐 Hourly Summary\n` +
    `  Each position: price, change, spread, SL/TP distance, P&L\n\n` +
    `<b>4:05 PM</b> — 🔴 Market Close\n` +
    `  End-of-day P&L for each position + total\n\n` +
    `<b>5:00 PM</b> — 🔄 IBKR Auto-Sync\n` +
    `  Pulls Flex Query, imports new trades, closes matched\n\n` +
    `<b>6:00 PM</b> — 📝 Journal Prompt\n` +
    `  Reply with your thoughts → saved as daily journal\n\n` +
    `<b>Real-time (every 60s during market)</b>\n` +
    `  ⚠️ Strike proximity warnings\n` +
    `  🛑 Stop loss / take profit triggers\n` +
    `  📊 Spread SL/TP alerts\n` +
    `  🔔 Watchlist entry target hits`
  );
}

async function cmdStatus() {
  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open');
  if (!trades.length) return tg('No open positions');

  const tickers = [...new Set(trades.map(t => t.ticker))];
  const quotes = await fetchQuotes(tickers);

  // Always fetch fresh spreads for on-demand commands
  tg('⏳ Fetching live spreads...');
  lastSpreadFetch = 0;
  await refreshAllSpreads(state.trades);

  const lines = trades.map(t => {
    const q = quotes[t.ticker];
    const dl = daysLeft(t.expiry);
    const spread = spreadCache[t.id];
    const price = q ? `$${q.price.toFixed(2)}` : '?';
    const chg = q ? `${q.changePct >= 0 ? '+' : ''}${q.changePct.toFixed(1)}%` : '';
    const pnlData = unrealizedPnl(t, q?.price);
    const pnlUp = pnlData ? pnlData.pnl >= 0 : true;
    const status = !q ? '⚪' : pnlData ? (pnlUp ? '🟢' : '🔴') : (q.price < t.shortStrike ? '🔴' : '🟢');
    const pnlStr = pnlData ? `${pnlData.pnl >= 0 ? '+' : '-'}$${Math.abs(pnlData.pnl).toFixed(0)}` : '';

    // Daily high/low
    const range = (q?.dayLow && q?.dayHigh) ? `L:$${q.dayLow.toFixed(2)} H:$${q.dayHigh.toFixed(2)}` : '';

    // Daily implied move from IV
    const impliedMove = (spread?.iv && q) ? `±$${(q.price * spread.iv * Math.sqrt(1/252)).toFixed(2)}` : '';

    // Chart SL / Spread SL / TP with % distance
    const slPct = (t.slPrice && q) ? ` (${((q.price - parseFloat(t.slPrice)) / q.price * 100).toFixed(1)}%)` : '';
    const tpPct = (t.tpPrice && q) ? ` (${((parseFloat(t.tpPrice) - q.price) / q.price * 100).toFixed(1)}%)` : '';
    const chartSL = t.slPrice ? `Chart SL: $${t.slPrice}${slPct}` : '';
    const spreadTPAway = (t.spreadTP && spread) ? ((spread.mid - parseFloat(t.spreadTP)) / spread.mid * 100).toFixed(0) : null;
    const spreadTP = (t.spreadTP && spread) ? `Spread TP: $${t.spreadTP} (${spreadTPAway}% away)` : '';
    const spreadSLAway = (t.spreadSL && spread) ? ((parseFloat(t.spreadSL) - spread.mid) / spread.mid * 100).toFixed(0) : null;
    const spreadSL = (t.spreadSL && spread) ? `Spread SL: $${t.spreadSL} (${spreadSLAway}% away)` : '';
    const tpStr = t.tpPrice ? `TP: $${t.tpPrice}${tpPct}` : '';

    let line = `${status} <b>${t.ticker}</b> ${price} (${chg})`;
    line += `\n   ${t.shortStrike}/${t.longStrike} x${t.contracts} | ${dl}DTE`;
    if (range) line += `\n   ${range}`;
    if (chartSL) line += `\n   ${chartSL}`;
    if (spreadTP) line += `\n   ${spreadTP}`;
    if (spreadSL) line += `\n   ${spreadSL}`;
    if (tpStr) line += `\n   ${tpStr}`;
    if (impliedMove) line += `\n   IV Move: ${impliedMove}`;
    if (pnlStr) line += `\n   P&L: ${pnlStr}`;

    return line;
  });

  tg(`📊 <b>Open Positions (${trades.length})</b>\n\n${lines.join('\n\n')}`);
}

async function cmdSpreads() {
  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open');
  if (!trades.length) return tg('No open positions');

  // Force a fresh spread refresh
  lastSpreadFetch = 0;
  await refreshAllSpreads(state.trades);

  const lines = trades.map(t => {
    const spread = spreadCache[t.id];
    const premium = t.premiumCollected || 0;
    const contracts = t.contracts || 1;
    const entrySpread = premium / (100 * contracts);

    if (!spread) return `${t.ticker} ${t.shortStrike}/${t.longStrike}: no data`;

    const costToClose = spread.mid * 100 * contracts;
    const pnl = premium - costToClose;
    const pnlSign = pnl >= 0 ? '+' : '-';
    const pnlPct = premium > 0 ? (pnl / premium * 100).toFixed(0) : '0';

    return `${pnl >= 0 ? '🟢' : '🔴'} <b>${t.ticker}</b> $${spread.mid.toFixed(2)} (entry $${entrySpread.toFixed(2)})\n   ${pnlSign}$${Math.abs(pnl).toFixed(0)} (${pnlPct}%) | Δ${spread.delta?.toFixed(2) || '?'}`;
  });

  tg(`📈 <b>Live Spreads</b>\n\n${lines.join('\n\n')}`);
}

async function cmdPnl() {
  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open');
  if (!trades.length) return tg('No open positions');

  const tickers = [...new Set(trades.map(t => t.ticker))];
  const quotes = await fetchQuotes(tickers);

  // Fetch fresh spreads
  lastSpreadFetch = 0;
  await refreshAllSpreads(state.trades);

  let totalPnl = 0;
  let totalPremium = 0;

  const lines = trades.map(t => {
    const q = quotes[t.ticker];
    const pnlData = unrealizedPnl(t, q?.price);
    const premium = t.premiumCollected || 0;
    totalPremium += premium;

    if (!pnlData) return `${t.ticker}: no data`;

    totalPnl += pnlData.pnl;
    const sign = pnlData.pnl >= 0 ? '+' : '-';
    const icon = pnlData.pnl >= 0 ? '🟢' : '🔴';

    return `${icon} ${t.ticker}: ${sign}$${Math.abs(pnlData.pnl).toFixed(0)} (${pnlData.source})`;
  });

  const totalSign = totalPnl >= 0 ? '+' : '-';
  const totalIcon = totalPnl >= 0 ? '🟢' : '🔴';

  tg(
    `💰 <b>P&L Summary</b>\n\n` +
    lines.join('\n') + '\n\n' +
    `${totalIcon} <b>Total: ${totalSign}$${Math.abs(totalPnl).toFixed(0)}</b>\n` +
    `Total premium: $${totalPremium.toFixed(0)}`
  );
}

async function cmdTicker(ticker) {
  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open' && t.ticker.toUpperCase() === ticker);
  if (!trades.length) return tg(`No open position for ${ticker}`);

  const quotes = await fetchQuotes([ticker]);
  const q = quotes[ticker];

  // Fetch fresh spreads for this ticker
  for (const t of trades) {
    try {
      const sv = await fetchSpreadValue(t);
      if (sv) spreadCache[t.id] = sv;
    } catch (e) { console.warn(`Spread ${ticker}: ${e.message}`); }
  }

  for (const t of trades) {
    const spread = spreadCache[t.id];
    const pnlData = unrealizedPnl(t, q?.price);
    const dl = daysLeft(t.expiry);
    const premium = t.premiumCollected || 0;
    const contracts = t.contracts || 1;
    const width = Math.abs(t.shortStrike - t.longStrike);
    const maxLoss = (width * 100 * contracts) - premium;
    const entrySpread = premium / (100 * contracts);

    let msg = `📋 <b>${t.ticker} ${t.tradeType}</b>\n\n`;
    msg += `Strikes: ${t.shortStrike}/${t.longStrike} x${contracts}\n`;
    msg += `Expiry: ${t.expiry} (${dl} DTE)\n`;
    msg += `Entry: ${t.entryDate}\n\n`;

    if (q) {
      const prox = ((q.price - t.shortStrike) / q.price * 100);
      msg += `📍 Price: $${q.price.toFixed(2)} (${q.changePct >= 0 ? '+' : ''}${q.changePct.toFixed(1)}%)\n`;
      msg += `Distance from strike: ${prox.toFixed(1)}%\n\n`;
    }

    if (spread) {
      msg += `📊 <b>Spread: $${spread.mid.toFixed(2)}</b> (${spread.ts})\n`;
      msg += `Bid/Ask: $${spread.low.toFixed(2)} / $${spread.high.toFixed(2)}\n`;
      if (spread.delta) msg += `Delta: ${spread.delta.toFixed(3)}\n`;
      if (spread.iv) msg += `IV: ${(spread.iv * 100).toFixed(1)}%\n`;
      msg += '\n';
    }

    msg += `💰 Premium: $${premium.toFixed(0)} ($${entrySpread.toFixed(2)}/spread)\n`;
    msg += `Max loss: $${maxLoss.toFixed(0)}\n`;

    if (pnlData) {
      const sign = pnlData.pnl >= 0 ? '+' : '-';
      msg += `P&L: ${sign}$${Math.abs(pnlData.pnl).toFixed(0)} (${pnlData.source})\n`;
    }

    if (t.slPrice) msg += `\n🛑 Price SL: $${t.slPrice}`;
    if (t.spreadTP) msg += `\n🎯 Spread TP: $${t.spreadTP}`;
    if (t.spreadSL) msg += `\n🚨 Spread SL: $${t.spreadSL}`;
    if (t.tpPrice) msg += `\n✅ TP: $${t.tpPrice}`;

    msg += `\n\n<i>/sl ${ticker} PRICE — price stop\n/ssl ${ticker} VALUE — spread stop (911)\n/tp ${ticker} PRICE — take profit</i>`;

    tg(msg);
  }
}

// ── Screenshot-to-Trade (Claude Vision) ──────────────────────────────────
async function handleScreenshot(msg) {
  try {
    tg('📸 Analyzing screenshot...');

    // Get the highest resolution photo
    const photo = msg.photo[msg.photo.length - 1];
    const fileResp = await fetch(`https://api.telegram.org/bot${TG_BOT}/getFile?file_id=${photo.file_id}`);
    const fileData = await fileResp.json();
    if (!fileData.ok) throw new Error('Failed to get file info');

    // Download the image
    const imageUrl = `https://api.telegram.org/file/bot${TG_BOT}/${fileData.result.file_path}`;
    const imageResp = await fetch(imageUrl);
    const imageBuffer = Buffer.from(await imageResp.arrayBuffer());
    const base64Image = imageBuffer.toString('base64');
    const mediaType = fileData.result.file_path.endsWith('.png') ? 'image/png' : 'image/jpeg';

    // Load state to check open positions
    const state = await loadState();
    const openTickers = (state.trades || []).filter(t => t.status === 'Open').map(t => t.ticker);

    // Send to Claude for parsing — include open positions for context
    const parsed = await parseScreenshotWithClaude(base64Image, mediaType, msg.caption || '', openTickers);
    if (!parsed) {
      return tg('Could not parse trade details from screenshot. Try /open or /close manually.');
    }

    // Auto-detect close: ticker matches an open position → assume close (unless caption says "adding" or "new")
    const caption = (msg.caption || '').toLowerCase();
    const isAdding = caption.includes('adding') || caption.includes('new') || caption.includes('open');
    const isClose = parsed.action === 'close' ||
      (openTickers.includes(parsed.ticker) && !isAdding);

    console.log(`[SCREENSHOT] ticker=${parsed.ticker} action=${parsed.action} openTickers=[${openTickers}] isClose=${isClose} isAdding=${isAdding}`);

    if (isClose && openTickers.includes(parsed.ticker)) {
      // It's a CLOSE — confirm closing the position
      const pnlStr = parsed.realizedPnl != null
        ? `\nRealized P&L: ${parsed.realizedPnl >= 0 ? '+' : '-'}$${Math.abs(parsed.realizedPnl).toFixed(0)}`
        : '';

      pendingTrade = {
        ...parsed,
        action: 'close',
        timestamp: Date.now()
      };

      tg(
        `📋 <b>Parsed CLOSE from screenshot:</b>\n\n` +
        `Ticker: <b>${parsed.ticker}</b>\n` +
        `${parsed.shortStrike}/${parsed.longStrike} x${parsed.contracts}${pnlStr}\n\n` +
        `<b>Close this position?</b>\n/yes — confirm (today's date)\n/yes yesterday — use yesterday's date\n/yes 3/5 — specify date\n/no — cancel`
      );
      return;
    }

    // It's an OPEN
    parsed.action = 'open';

    // If premium is missing, ask for it
    if (!parsed.premium) {
      pendingTrade = { ...parsed, timestamp: Date.now(), needsPremium: true };
      tg(
        `📋 <b>Parsed from screenshot:</b>\n\n` +
        `Ticker: <b>${parsed.ticker}</b>\n` +
        `Type: ${parsed.tradeType}\n` +
        `Strikes: ${parsed.shortStrike}/${parsed.longStrike} x${parsed.contracts}\n` +
        `Expiry: ${parsed.expiry} (${parsed.dte} DTE)\n\n` +
        `⚠️ Could not determine premium.\n` +
        `Reply with the total premium (e.g. <i>2500</i>)`
      );
      return;
    }

    // Store as pending trade
    pendingTrade = {
      ...parsed,
      timestamp: Date.now()
    };

    const width = Math.abs(parsed.shortStrike - parsed.longStrike);
    const maxLoss = (width * 100 * parsed.contracts) - parsed.premium;
    const perSpread = (parsed.premium / (100 * parsed.contracts)).toFixed(2);

    tg(
      `📋 <b>Parsed from screenshot:</b>\n\n` +
      `Ticker: <b>${parsed.ticker}</b>\n` +
      `Type: ${parsed.tradeType}\n` +
      `Strikes: ${parsed.shortStrike}/${parsed.longStrike} x${parsed.contracts}\n` +
      `Expiry: ${parsed.expiry} (${parsed.dte} DTE)\n` +
      `Premium: $${parsed.premium.toFixed(0)} ($${perSpread}/spread)\n` +
      `Max loss: $${maxLoss.toFixed(0)}\n\n` +
      `<b>Open this trade?</b>\n/yes — confirm\n/no — cancel`
    );
  } catch (e) {
    console.error('[SCREENSHOT]', e.message);
    tg(`Screenshot error: ${e.message}\nTry /open manually.`);
  }
}

async function parseScreenshotWithClaude(base64Image, mediaType, caption, openTickers = []) {
  const today = formatDate(new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })));

  const prompt = `You are parsing a screenshot from a trading platform (IBKR, OptionStrat, TastyTrade, or similar) to extract option spread trade details.

FIRST: Determine if this screenshot shows OPENING a new position or CLOSING an existing position.
Signs of a CLOSE/EXIT:
- IBKR: "BOT" (bought back) the short leg AND "SLD" (sold) the long leg to close
- Shows "Realized P&L" or "Realized PnL"
- Trade confirmation showing a closing transaction
- The action is REVERSING the spread (buying back what was sold, selling what was bought)
Signs of an OPEN/ENTRY:
- IBKR: "SLD" (sold) the short leg AND "BOT" (bought) the long leg to open
- Shows "Credit" received for opening
- Order preview or new position setup

Return ONLY valid JSON, no markdown, no explanation:
{
  "action": "open" or "close",
  "ticker": "AAPL",
  "side": "P" or "C",
  "shortStrike": 220,
  "longStrike": 215,
  "contracts": 3,
  "premium": 900,
  "realizedPnl": null,
  "expiry": "2026-03-14"
}

CRITICAL RULES:
- action: "open" if entering a new position, "close" if exiting/closing
- This is a CREDIT SPREAD (two legs). IBKR shows each leg on a separate line — you must combine them.
- ticker: the stock symbol (uppercase)
- side: "P" for puts, "C" for calls. Look for "PUT" or "CALL" in the option description.
- shortStrike: the SHORT strike (higher strike for puts, lower for calls)
- longStrike: the LONG strike (lower strike for puts, higher for calls)
- contracts: number of spreads (both legs should have same quantity)
- premium: NET TOTAL credit collected in dollars (for OPENS only). Set null for closes.
  IBKR PREMIUM CALCULATION — READ CAREFULLY:
  IBKR shows THREE numbers per leg: FILL PRICE (per-contract, e.g. $13.73), AMOUNT (total, e.g. $2,745), and COMMISSION.
  The AMOUNT column already includes the quantity multiplier (fill × qty × 100). DO NOT multiply it by contracts again.
  To calculate premium, use ONE of these methods:
  Method 1 (FILL PRICES): (sold_fill - bought_fill) × 100 × contracts. Example: ($13.73 - $3.57) × 100 × 2 = $2,032
  Method 2 (AMOUNTS): sold_amount - bought_amount. Example: $2,745 - $714 = $2,031
  Both methods give the same result. NEVER multiply the AMOUNT by contracts — it's already total.
  A valid premium for a 20-wide spread with 2 contracts MUST be less than $4,000 (width × 100 × contracts).
- realizedPnl: the realized P&L in dollars (for CLOSES only). Positive = profit, negative = loss. Set null for opens.
  * IBKR may show this as "Realized PnL" or you can calculate from the closing prices
- expiry: in YYYY-MM-DD format. IBKR format is often "20MAR26" or "MAR 20 '26" = 2026-03-20. Today is ${today}.

SANITY CHECK: max_loss = (shortStrike - longStrike) × 100 × contracts - premium. This MUST be positive. If your calculated premium makes max_loss negative, you double-counted — recalculate.

Currently open positions: ${openTickers.length > 0 ? openTickers.join(', ') : 'none'}
If the ticker matches an open position, it's MORE LIKELY a close. Look carefully for closing indicators.

If the caption says anything, use it as additional context: "${caption}"

If you cannot determine a field with confidence, set it to null. Return ONLY the JSON object.`;

  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01'
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 500,
      messages: [{
        role: 'user',
        content: [
          { type: 'image', source: { type: 'base64', media_type: mediaType, data: base64Image } },
          { type: 'text', text: prompt }
        ]
      }]
    })
  });

  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Claude API ${r.status}: ${err}`);
  }

  const data = await r.json();
  const text = data.content?.[0]?.text || '';
  console.log('[CLAUDE]', text);

  // Parse the JSON response
  let parsed;
  try {
    // Try to extract JSON from the response (in case Claude wraps it)
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) throw new Error('No JSON found');
    parsed = JSON.parse(jsonMatch[0]);
  } catch (e) {
    console.error('[CLAUDE-PARSE]', e.message, text);
    return null;
  }

  // Validate required fields
  if (!parsed.ticker || !parsed.shortStrike || !parsed.longStrike) {
    tg(`⚠️ Could not extract all fields:\n${JSON.stringify(parsed, null, 2)}`);
    return null;
  }

  // Default contracts to 1 if not found
  if (!parsed.contracts) parsed.contracts = 1;

  // Sanity check: premium can't exceed max possible credit (width × 100 × contracts)
  if (parsed.premium && parsed.shortStrike && parsed.longStrike && parsed.contracts) {
    const width = Math.abs(parsed.shortStrike - parsed.longStrike);
    const maxCredit = width * 100 * parsed.contracts;
    if (parsed.premium > maxCredit) {
      console.log(`[CLAUDE-FIX] Premium $${parsed.premium} exceeds max credit $${maxCredit} — likely double-counted, halving`);
      // Premium was likely double-counted (IBKR total amounts × contracts again)
      parsed.premium = Math.round(parsed.premium / parsed.contracts);
    }
  }

  // Calculate derived fields
  const side = (parsed.side || 'P').toUpperCase();
  const tradeType = side === 'P' ? 'Bull Put Spread' : 'Bear Call Spread';
  const expiry = parsed.expiry || today;
  const expiryDate = new Date(expiry + 'T16:00:00-05:00');
  const entryDateObj = new Date(today + 'T09:30:00-05:00');
  const dte = Math.ceil((expiryDate - entryDateObj) / 86400000);

  return {
    action: parsed.action || 'open',
    ticker: parsed.ticker.toUpperCase(),
    tradeType,
    side,
    shortStrike: parsed.shortStrike,
    longStrike: parsed.longStrike,
    contracts: parsed.contracts,
    premium: parsed.premium || 0,
    realizedPnl: parsed.realizedPnl || null,
    expiry,
    dte
  };
}

async function cmdConfirmTrade(confirmed, extraArgs = []) {
  if (!pendingTrade) {
    return tg('No pending trade to confirm. Send a screenshot or use /open.');
  }

  // Expire pending trades after 5 minutes
  if (Date.now() - pendingTrade.timestamp > 5 * 60 * 1000) {
    pendingTrade = null;
    return tg('Pending trade expired. Send a new screenshot.');
  }

  if (!confirmed) {
    pendingTrade = null;
    return tg('Trade cancelled.');
  }

  const p = pendingTrade;
  pendingTrade = null;

  if (p.action === 'close') {
    // Close the position — pass optional date from /yes yesterday or /yes 3/5
    const pnlStr = p.realizedPnl != null ? String(p.realizedPnl) : '';
    const parts = ['/close', p.ticker];
    if (pnlStr) parts.push(pnlStr);
    const dateArg = extraArgs.join(' ').trim(); // e.g. "yesterday", "3/5", "March 4"
    if (dateArg) parts.push(dateArg);
    return await cmdClose(parts);
  }

  // Open a new position (skip checklist since user already confirmed)
  const openParts = p.openParts || ['/open', p.ticker, `${p.shortStrike}/${p.longStrike}${p.side}`, p.expiry, String(p.contracts), String(p.premium)];
  return await cmdOpen(openParts, true);
}

// ── Parse expiry date from shorthand ──────────────────────────────────────
function parseExpiry(input, allowPast = false) {
  const lower = input.toLowerCase();
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));

  if (lower === '0dte') {
    return formatDate(et);
  }

  // Day names: fri, mon, tue, wed, thu
  const dayMap = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
  const nextPrefix = lower.startsWith('next-');
  const dayName = nextPrefix ? lower.slice(5) : lower;

  if (dayMap[dayName] !== undefined) {
    const target = dayMap[dayName];
    const current = et.getDay();
    let daysAhead = target - current;
    if (daysAhead <= 0) daysAhead += 7;
    if (nextPrefix && daysAhead <= 7) daysAhead += 7;
    const d = new Date(et);
    d.setDate(d.getDate() + daysAhead);
    return formatDate(d);
  }

  // M/D or MM/DD format (assumes current year, or next year if date has passed — unless allowPast)
  if (/^\d{1,2}\/\d{1,2}$/.test(input)) {
    const [m, d] = input.split('/').map(Number);
    let year = et.getFullYear();
    if (!allowPast) {
      const candidate = new Date(year, m - 1, d);
      if (candidate < et) year++;
    }
    return `${year}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  // "March 4", "Mar 4", "march4", "mar4" etc.
  const monthMap = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
    january: 1, february: 2, march: 3, april: 4, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 };
  const monthMatch = lower.match(/^([a-z]+)\s*(\d{1,2})$/);
  if (monthMatch && monthMap[monthMatch[1]]) {
    const m = monthMap[monthMatch[1]];
    const d = parseInt(monthMatch[2]);
    let year = et.getFullYear();
    return `${year}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  // Full date YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(input)) return input;

  return null;
}

function formatDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ── /open TICKER SHORT/LONGP EXPIRY QTY PREMIUM ──────────────────────────
async function cmdOpen(parts, skipChecklist) {
  // /open NFLX 950/940P 3/7 5 2500
  if (parts.length < 6) {
    return tg(
      `<b>Usage:</b> /open TICKER SHORT/LONGP EXPIRY QTY PREMIUM\n\n` +
      `<b>Examples:</b>\n` +
      `/open NFLX 950/940P 3/7 5 2500\n` +
      `/open AAPL 220/215P 0dte 3 900\n` +
      `/open TSLA 280/270P fri 2 1200\n` +
      `/open MSFT 430/440C next-fri 4 1600\n\n` +
      `<b>Expiry formats:</b> 3/7, 0dte, fri, next-fri, 2026-03-07`
    );
  }

  const ticker = parts[1].toUpperCase();

  // Parse strikes + side: 950/940P or 430/440C
  const strikeMatch = parts[2].match(/^(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)(P|C)$/i);
  if (!strikeMatch) {
    return tg(`Invalid strikes: ${parts[2]}\nFormat: SHORT/LONGP or SHORT/LONGC\nExample: 950/940P`);
  }
  const shortStrike = parseFloat(strikeMatch[1]);
  const longStrike = parseFloat(strikeMatch[2]);
  const side = strikeMatch[3].toUpperCase();
  const tradeType = side === 'P' ? 'Bull Put Spread' : 'Bear Call Spread';

  // Parse expiry
  const expiry = parseExpiry(parts[3]);
  if (!expiry) {
    return tg(`Invalid expiry: ${parts[3]}\nFormats: 3/7, 0dte, fri, next-fri, 2026-03-07`);
  }

  const contracts = parseInt(parts[4]);
  if (isNaN(contracts) || contracts <= 0) {
    return tg(`Invalid quantity: ${parts[4]}`);
  }

  const premium = parseFloat(parts[5]);
  if (isNaN(premium) || premium <= 0) {
    return tg(`Invalid premium: ${parts[5]}`);
  }

  // Load state
  const state = await loadState();
  const today = formatDate(new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })));
  const expiryDate = new Date(expiry + 'T16:00:00-05:00');
  const entryDateObj = new Date(today + 'T09:30:00-05:00');
  const dte = Math.ceil((expiryDate - entryDateObj) / 86400000);

  const newTrade = {
    ticker,
    tradeType,
    shortStrike,
    longStrike,
    contracts,
    premiumCollected: premium,
    entryDate: today,
    expiry,
    status: 'Open',
    deltaEntry: '',
    pop: '',
    entryType: '',
    thesis: '',
    chartLink: '',
    tpPrice: '',
    slPrice: '',
    tpLevel: '',
    slLevel: '',
    dteAtEntry: dte,
    createdAt: today,
    exitDate: null,
    realizedPnl: null,
    exitReason: '',
    ruleAdherence: '',
    ruleBreak: '',
    thesisAccuracy: '',
    deltaClose: '',
    mae: '',
    mfe: '',
    lessonLearned: '',
    sentiment: side === 'P' ? 'Bullish' : 'Bearish',
    strategyType: '',
    earningsFlag: '',
    spreadSL: '',
    rolls: [],
    journal: [],
    _autoMAE: { pnl: null, price: null, date: null, src: null },
    _autoMFE: { pnl: null, price: null, date: null, src: null }
  };

  // Run pre-trade checklist
  if (!skipChecklist) {
    const result = runPreTradeChecklist(newTrade, state);
    const checklistMsg = formatChecklist(ticker, newTrade, result);

    if (result.flags.length > 0) {
      // Gate: store as pending and wait for /yes
      pendingTrade = {
        ticker, shortStrike, longStrike, side, contracts, premium, expiry, tradeType, dteAtEntry: dte,
        openParts: parts, // store original parts for re-execution
        timestamp: Date.now()
      };
      return tg(checklistMsg);
    } else {
      // No flags — show clean checklist and proceed
      tg(checklistMsg);
    }
  }

  // Create trade
  if (!state.nextId) state.nextId = Math.max(...(state.trades || []).map(t => t.id || 0)) + 1;
  newTrade.id = state.nextId++;
  if (!state.trades) state.trades = [];
  state.trades.push(newTrade);

  await saveState(state);
  lastState = state;

  const width = Math.abs(shortStrike - longStrike);
  const maxLoss = (width * 100 * contracts) - premium;
  const perSpread = (premium / (100 * contracts)).toFixed(2);

  tg(
    `✅ <b>TRADE OPENED — ${ticker}</b>\n\n` +
    `${tradeType}\n` +
    `${shortStrike}/${longStrike} x${contracts}\n` +
    `Expiry: ${expiry} (${dte} DTE)\n` +
    `Premium: $${premium.toFixed(0)} ($${perSpread}/spread)\n` +
    `Max loss: $${maxLoss.toFixed(0)}\n\n` +
    `<i>Set stops:\n/sl ${ticker} PRICE\n/ssl ${ticker} VALUE\n/tp ${ticker} PRICE</i>`
  );
}

// ── /close TICKER [PNL] [DATE] ────────────────────────────────────────────
async function cmdClose(parts) {
  if (parts.length < 2) {
    return tg(`<b>Usage:</b> /close TICKER [PNL] [DATE]\n\nExamples:\n/close NFLX\n/close NFLX 1200\n/close NFLX -800 3/6\n/close NFLX 1200 yesterday`);
  }

  const ticker = parts[1].toUpperCase();
  const realizedPnl = parts[2] ? parseFloat(parts[2]) : null;

  // Parse optional date (3rd or 4th arg)
  const today = formatDate(new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })));
  let closeDate = today;
  // Date could be multi-word like "March 4" — join remaining parts
  const dateFromPos3 = parts.slice(3).join(' ').trim() || null;
  const dateArg = dateFromPos3 || (parts[2] && isNaN(parseFloat(parts[2])) ? parts[2] : null);
  if (dateArg) {
    const lower = dateArg.toLowerCase();
    if (lower === 'yesterday') {
      const d = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
      d.setDate(d.getDate() - 1);
      closeDate = formatDate(d);
    } else {
      const parsed = parseExpiry(dateArg, true); // allowPast=true for close dates
      if (parsed) closeDate = parsed;
    }
  }

  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open' && t.ticker === ticker);
  if (!trades.length) return tg(`No open position for ${ticker}`);

  let closed = 0;
  for (const t of trades) {
    t.status = 'Closed';
    t.exitDate = closeDate;
    if (realizedPnl !== null && !isNaN(realizedPnl)) {
      t.realizedPnl = realizedPnl;
    }
    closed++;

    // Clean up spread cache
    delete spreadCache[t.id];
    delete prevSpreadCache[t.id];
  }

  await saveState(state);
  lastState = state;

  const pnlStr = realizedPnl !== null ? `\nRealized P&L: ${realizedPnl >= 0 ? '+' : '-'}$${Math.abs(realizedPnl).toFixed(0)}` : '\n<i>Tip: /close TICKER PNL to record P&L</i>';

  tg(
    `🔒 <b>TRADE CLOSED — ${ticker}</b>\n\n` +
    `${closed} position(s) closed on ${today}${pnlStr}\n\n` +
    `<i>Dashboard will update on next refresh</i>`
  );
}

async function cmdSpreadLevel(parts, type) {
  // /ssl NVDA 75  → spread SL at 75% of premium
  // /stp NVDA 50  → spread TP at 50% of premium
  const cmd = type === 'tp' ? '/stp' : '/ssl';
  const label = type === 'tp' ? 'Spread TP' : 'Spread SL';
  if (parts.length < 3) {
    return tg(`<b>Usage:</b> ${cmd} TICKER PERCENT\nExample: ${cmd} NVDA ${type === 'tp' ? '50' : '75'}\n\nSets ${label} as a % of premium collected.`);
  }

  const ticker = parts[1].toUpperCase();
  const pct = parseFloat(parts[2]);
  if (isNaN(pct) || pct <= 0) return tg(`Invalid percentage: ${parts[2]}`);

  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open' && t.ticker === ticker);
  if (!trades.length) return tg(`No open position for ${ticker}`);

  let updated = 0;
  for (const t of trades) {
    const premium = t.premiumCollected || 0;
    const contracts = t.contracts || 1;
    if (!premium) continue;
    const perSpread = premium / (100 * contracts);

    if (type === 'tp') {
      // TP: buy back when spread decays to this value (e.g. 50% → keep 50% profit)
      const val = perSpread * (1 - pct / 100);
      t.spreadTP = val.toFixed(2);
      t.spreadTPPct = String(pct);
    } else {
      // SL: close when spread rises to this value (e.g. 75% loss of premium)
      const val = perSpread * (pct / 100);
      t.spreadSL = val.toFixed(2);
      t.spreadSLPct = String(pct);
    }
    updated++;
  }

  await saveState(state);
  lastState = state;

  const t = trades[0];
  const dollarVal = type === 'tp' ? t.spreadTP : t.spreadSL;
  const emoji = type === 'tp' ? '🎯' : '🚨';

  tg(
    `${emoji} <b>${ticker} ${label} set</b>\n\n` +
    `${pct}% → $${dollarVal}/spread\n` +
    `<i>Dashboard will update on next refresh</i>`
  );
}

async function cmdUpdateField(parts, field, label) {
  // /sl NVDA 192  or  /tp CAT 0.40
  if (parts.length < 3) {
    return tg(`Usage: ${parts[0]} TICKER VALUE\nExample: ${parts[0]} NVDA 192`);
  }

  const ticker = parts[1].toUpperCase();
  const value = parseFloat(parts[2]);
  if (isNaN(value)) return tg(`Invalid value: ${parts[2]}`);

  const state = await loadState();
  const trades = (state.trades || []).filter(t => t.status === 'Open' && t.ticker === ticker);
  if (!trades.length) return tg(`No open position for ${ticker}`);

  // Update all open trades for this ticker
  let updated = 0;
  for (const t of trades) {
    t[field] = value;
    updated++;
  }

  await saveState(state);
  lastState = state;

  // Clear relevant alerts so they re-trigger at new levels
  for (const t of trades) {
    const key = t.id + '-';
    alertsSent.delete(key + 'sl');
    alertsSent.delete(key + 'tp');
    alertsSent.delete(key + 'spreadSL');
  }

  tg(`✅ <b>${ticker} ${label} → $${value}</b>\n${updated} trade(s) updated\n\n<i>Card will update on next refresh</i>`);
}

// ── Hourly Summary ──────────────────────────────────────────────────────────
async function sendHourlySummary() {
  try {
    const state = await loadState();
    const trades = (state.trades || []).filter(t => t.status === 'Open');
    if (!trades.length) return;

    const tickers = [...new Set(trades.map(t => t.ticker))];
    const quotes = await fetchQuotes(tickers);

    // Refresh spreads
    lastSpreadFetch = 0;
    await refreshAllSpreads(state.trades);

    const etHour = new Date().toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });

    const lines = trades.map(t => {
      const q = quotes[t.ticker];
      if (!q) return `  ${t.ticker}: no data`;

      const spread = spreadCache[t.id];
      const dl = daysLeft(t.expiry);
      const pnlData = unrealizedPnl(t, q.price);
      const icon = pnlData ? (pnlData.pnl >= 0 ? '🟢' : '🔴') : '⚪';
      const chg = `${q.changePct >= 0 ? '+' : ''}${q.changePct.toFixed(1)}%`;

      let line = `${icon} <b>${t.ticker}</b> $${q.price.toFixed(2)} (${chg})`;
      line += `\n   ${t.shortStrike}/${t.longStrike} x${t.contracts} | ${dl}DTE`;

      // Daily range
      if (q.dayLow && q.dayHigh) line += `\n   L:$${q.dayLow.toFixed(2)} H:$${q.dayHigh.toFixed(2)}`;

      // Chart SL with % distance
      if (t.slPrice) {
        const slPct = ((q.price - parseFloat(t.slPrice)) / q.price * 100).toFixed(1);
        line += `\n   Chart SL: $${t.slPrice} (${slPct}%)`;
      }

      // Spread TP with % away
      if (t.spreadTP && spread) {
        const away = ((spread.mid - parseFloat(t.spreadTP)) / spread.mid * 100).toFixed(0);
        line += `\n   Spread TP: $${t.spreadTP} (${away}% away)`;
      }

      // Spread SL with % away
      if (t.spreadSL && spread) {
        const away = ((parseFloat(t.spreadSL) - spread.mid) / spread.mid * 100).toFixed(0);
        line += `\n   Spread SL: $${t.spreadSL} (${away}% away)`;
      }

      // TP with % distance
      if (t.tpPrice) {
        const tpPct = ((parseFloat(t.tpPrice) - q.price) / q.price * 100).toFixed(1);
        line += `\n   TP: $${t.tpPrice} (${tpPct}%)`;
      }

      // IV implied move
      const impliedMove = (spread?.iv && q) ? `±$${(q.price * spread.iv * Math.sqrt(1/252)).toFixed(2)}` : '';
      if (impliedMove) line += `\n   IV Move: ${impliedMove}`;

      // P&L
      if (pnlData) {
        const sign = pnlData.pnl >= 0 ? '+' : '-';
        line += `\n   P&L: ${sign}$${Math.abs(pnlData.pnl).toFixed(0)}`;
      }

      return line;
    });

    tg(`🕐 <b>${etHour} ET</b>\n\n${lines.join('\n\n')}`);

    // Snapshot current values for next hour's comparison
    prevSpreadCache = { ...spreadCache };
    for (const t of tickers) {
      if (quotes[t]) prevQuoteCache[t] = quotes[t].price;
    }
  } catch (e) {
    console.error('[HOURLY]', e.message);
  }
}

// ── Market Open/Close ───────────────────────────────────────────────────────
async function sendMarketOpen() {
  try {
    const state = await loadState();
    lastState = state;
    const openTrades = (state.trades || []).filter(t => t.status === 'Open');
    if (openTrades.length === 0) return;

    const tickers = [...new Set(openTrades.map(t => t.ticker))];
    const tradesWithSL = openTrades.filter(t => t.slPrice).length;
    const tradesWithTP = openTrades.filter(t => t.tpPrice).length;

    alertsSent.clear();

    tg(
      `🟢 <b>Market Open — Alert Server Active</b>\n\n` +
      `Monitoring ${openTrades.length} positions (${tickers.length} tickers)\n` +
      `SL set: ${tradesWithSL} | TP set: ${tradesWithTP}\n` +
      `Spreads checked every 5 min\n` +
      `Hourly summaries enabled\n\n` +
      `<i>Type /help for commands</i>`
    );

    // Fetch initial spreads
    lastSpreadFetch = 0;
    await refreshAllSpreads(state.trades);
  } catch (e) {
    console.error('[OPEN]', e.message);
  }
}

async function sendMarketClose() {
  try {
    const state = await loadState();
    const trades = (state.trades || []).filter(t => t.status === 'Open');
    if (trades.length === 0) return;

    const tickers = [...new Set(trades.map(t => t.ticker))];
    const quotes = await fetchQuotes(tickers);

    // Force final spread refresh
    lastSpreadFetch = 0;
    await refreshAllSpreads(state.trades);

    let totalPnl = 0;
    const lines = trades.map(t => {
      const q = quotes[t.ticker];
      if (!q) return `  ${t.ticker}: no quote`;

      const spread = spreadCache[t.id];
      const pnlData = unrealizedPnl(t, q.price);
      const prox = ((q.price - t.shortStrike) / q.price * 100);
      const dl = daysLeft(t.expiry);
      const icon = pnlData ? (pnlData.pnl >= 0 ? '🟢' : '🔴') : (q.price < t.shortStrike ? '🔴' : '🟢');

      if (pnlData) totalPnl += pnlData.pnl;

      let line = `${icon} <b>${t.ticker}</b> $${q.price.toFixed(2)} (${q.changePct >= 0 ? '+' : ''}${q.changePct.toFixed(1)}%)`;
      line += `\n   ${t.shortStrike}/${t.longStrike} | ${prox.toFixed(1)}% from strike | ${dl}DTE`;
      if (spread) line += `\n   Spread: $${spread.mid.toFixed(2)}`;
      if (pnlData) {
        const sign = pnlData.pnl >= 0 ? '+' : '-';
        line += ` | P&L: ${sign}$${Math.abs(pnlData.pnl).toFixed(0)}`;
      }
      return line;
    });

    const totalSign = totalPnl >= 0 ? '+' : '-';

    tg(
      `🔴 <b>Market Closed — Daily Summary</b>\n\n` +
      lines.join('\n\n') + '\n\n' +
      `<b>Total P&L: ${totalSign}$${Math.abs(totalPnl).toFixed(0)}</b>\n` +
      `Alerts sent today: ${alertsSent.size}`
    );
  } catch (e) {
    console.error('[CLOSE]', e.message);
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function daysLeft(expiry) {
  if (!expiry) return Infinity;
  const exp = new Date(expiry + 'T16:00:00-05:00');
  return Math.ceil((exp - new Date()) / 86400000);
}

function isMarketHours() {
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 565 && minutes <= 965; // 9:25 AM to 4:05 PM
}

function getETDate() {
  return new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' });
}

function ts() {
  return new Date().toISOString().replace('T', ' ').slice(0, 19);
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── Main Check Cycle ────────────────────────────────────────────────────────
async function runCheck() {
  const today = getETDate();
  if (lastMarketDate && lastMarketDate !== today) {
    alertsSent.clear();
    console.log(`[${ts()}] New trading day — alerts reset`);
  }
  lastMarketDate = today;

  try {
    const state = await loadState();
    lastState = state;

    const openTrades = (state.trades || []).filter(t => t.status === 'Open');
    if (openTrades.length === 0) {
      console.log(`[${ts()}] No open positions`);
      return;
    }

    const tickers = [...new Set(openTrades.map(t => t.ticker))];

    // Fetch stock quotes (every cycle — Yahoo is free)
    const quotes = await fetchQuotes(tickers);

    // Fetch spread values (every 5 min — MarketData.app is metered)
    await refreshAllSpreads(state.trades);

    // Check all alerts
    const alertCount = checkAlerts(state, quotes);

    // Track MAE/MFE for all open trades
    // Collect changes in a map, then merge into a FRESH state read to avoid
    // overwriting dashboard edits (watchlist, notes, etc.) with stale data.
    const maeMfeUpdates = {}; // { tradeId: { mae, mfe } }
    for (const t of openTrades) {
      const q = quotes[t.ticker];
      if (!q) continue;
      const pnlData = unrealizedPnl(t, q.price);
      if (!pnlData) continue;
      const pnl = pnlData.pnl;
      const today = getETDate();
      const src = pnlData.source;
      const mae = t._autoMAE || { pnl: null, price: null, date: null, src: null };
      const mfe = t._autoMFE || { pnl: null, price: null, date: null, src: null };
      let changed = false;
      const newMae = (mae.pnl === null || pnl < mae.pnl) ? { pnl, price: q.price, date: today, src } : mae;
      const newMfe = (mfe.pnl === null || pnl > mfe.pnl) ? { pnl, price: q.price, date: today, src } : mfe;
      if (newMae !== mae || newMfe !== mfe) {
        maeMfeUpdates[t.id] = { mae: newMae, mfe: newMfe };
      }
    }
    if (Object.keys(maeMfeUpdates).length > 0) {
      // Re-read fresh state so we don't overwrite dashboard changes
      const freshState = await loadState();
      for (const trade of (freshState.trades || [])) {
        const upd = maeMfeUpdates[trade.id];
        if (upd) {
          trade._autoMAE = upd.mae;
          trade._autoMFE = upd.mfe;
        }
      }
      await saveState(freshState);
      lastState = freshState;
    }

    // Log
    const prices = tickers.map(t => `${t}:${quotes[t]?.price?.toFixed(2) || '?'}`).join(' ');
    const spreadCount = Object.keys(spreadCache).length;
    console.log(`[${ts()}] ${Object.keys(quotes).length}/${tickers.length} quotes | ${spreadCount} spreads | ${alertCount} alerts | ${prices}`);

    consecutiveErrors = 0;
  } catch (e) {
    consecutiveErrors++;
    console.error(`[${ts()}] ERROR (${consecutiveErrors}x): ${e.message}`);

    if (consecutiveErrors === 5) {
      tg(`🚨 <b>ALERT SERVER ERROR</b>\n${e.message}\n5 consecutive failures — check VPS`);
    }
  }
}

// ── Telegram Polling Loop ───────────────────────────────────────────────────
async function telegramLoop() {
  console.log('[TG] Command listener started');
  while (true) {
    await pollTelegram();
    await sleep(1000);
  }
}

// ── Auto IBKR Flex Query Sync ─────────────────────────────────────────────

async function autoSyncIBKR() {
  try {
    const state = await loadState();
    const token = state.ibkrToken;
    const queryId = state.ibkrQueryId;
    if (!token || !queryId) {
      console.log('[IBKR-SYNC] No token/queryId configured — skipping');
      return;
    }

    console.log('[IBKR-SYNC] Starting auto sync...');

    // Step 1: Request report
    const reqUrl = `https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest?t=${token}&q=${queryId}&v=3`;
    const reqResp = await fetch(reqUrl).then(r => r.text());

    const refMatch = reqResp.match(/<ReferenceCode>(\d+)<\/ReferenceCode>/);
    if (!refMatch) {
      const errMsg = reqResp.match(/<ErrorMessage>([^<]+)<\/ErrorMessage>/);
      throw new Error(errMsg ? errMsg[1] : 'Failed to get reference code');
    }
    const refCode = refMatch[1];
    console.log('[IBKR-SYNC] Got reference', refCode);

    // Step 2: Poll for report
    let data = null;
    for (let attempt = 0; attempt < 5; attempt++) {
      await new Promise(r => setTimeout(r, 3000 + attempt * 2000));
      const fetchUrl = `https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement?q=${refCode}&t=${token}&v=3`;
      const fetchResp = await fetch(fetchUrl).then(r => r.text());
      if (fetchResp.includes('Statement generation in progress') || fetchResp.includes('Please try again')) continue;
      data = fetchResp;
      break;
    }
    if (!data) throw new Error('IBKR took too long to generate report');

    // Step 3: Parse trades (XML regex-based for Node.js)
    const optionTrades = parseFlexXML(data);
    if (!optionTrades.length) {
      console.log('[IBKR-SYNC] No option trades found');
      return;
    }

    // Step 4: Pair into spreads
    const spreads = pairSpreads(optionTrades);
    console.log(`[IBKR-SYNC] ${spreads.length} spreads from ${optionTrades.length} legs`);

    // Step 5: Reconcile — close existing open trades
    let closedExisting = 0;
    const closes = optionTrades.filter(t => t.isClose);
    for (const openTrade of (state.trades || []).filter(t => t.status === 'Open')) {
      const isPut = (openTrade.tradeType || '').toLowerCase().includes('put');
      const side = isPut ? 'P' : 'C';
      const shortCloses = closes.filter(c => c.ticker === openTrade.ticker && c.expiry === openTrade.expiry && c.strike === openTrade.shortStrike && c.side === side);
      const longCloses = closes.filter(c => c.ticker === openTrade.ticker && c.expiry === openTrade.expiry && c.strike === openTrade.longStrike && c.side === side);
      if (shortCloses.length > 0 && longCloses.length > 0) {
        const lastClose = shortCloses[shortCloses.length - 1];
        const closeCost = Math.abs(shortCloses.reduce((s, c) => s + c.proceeds, 0) + longCloses.reduce((s, c) => s + c.proceeds, 0));
        const closeComm = shortCloses.reduce((s, c) => s + Math.abs(c.commission), 0) + longCloses.reduce((s, c) => s + Math.abs(c.commission), 0);
        const pnl = Math.round(((openTrade.premiumCollected || 0) - closeCost - closeComm) * 100) / 100;
        openTrade.status = 'Closed';
        openTrade.exitDate = lastClose.dateOnly;
        openTrade.realizedPnl = pnl;
        closedExisting++;
      }
    }

    // Also check spreads that match existing open trades
    for (const s of spreads) {
      if (s.status !== 'Closed') continue;
      const existing = (state.trades || []).find(t =>
        t.ticker === s.ticker && t.expiry === s.expiry &&
        t.shortStrike === s.shortStrike && t.longStrike === s.longStrike && t.status === 'Open'
      );
      if (existing) {
        existing.status = 'Closed';
        existing.exitDate = s.exitDate;
        existing.realizedPnl = s.realizedPnl;
        closedExisting++;
      }
    }

    // Step 6: Add new trades
    const isDupe = (ex, nw) => ex.ticker === nw.ticker && ex.expiry === nw.expiry &&
      ex.shortStrike === nw.shortStrike && ex.longStrike === nw.longStrike && ex.entryDate === nw.entryDate;
    const newSpreads = spreads.filter(s => !(state.trades || []).some(t => isDupe(t, s)));

    // Assign IDs and add
    let nextId = state.nextId || (state.trades || []).length + 1;
    for (const s of newSpreads) {
      s.id = nextId++;
      state.trades.push(s);
    }
    state.nextId = nextId;

    if (closedExisting > 0 || newSpreads.length > 0) {
      await saveState(state);
      const parts = [];
      if (newSpreads.length) parts.push(`${newSpreads.length} new trade(s) imported`);
      if (closedExisting) parts.push(`${closedExisting} trade(s) updated to Closed`);
      await tg(`🔄 <b>IBKR Auto-Sync</b>\n${parts.join(', ')}`);
      console.log(`[IBKR-SYNC] ${parts.join(', ')}`);
    } else {
      console.log('[IBKR-SYNC] Everything in sync — no changes');
    }
  } catch (e) {
    console.error('[IBKR-SYNC]', e.message);
  }
}

// XML regex parser for IBKR Flex Query (Node.js — no DOMParser)
function parseFlexXML(xmlText) {
  // Check for error
  const errMatch = xmlText.match(/<ErrorCode>(\d+)<\/ErrorCode>\s*<ErrorMessage>([^<]+)<\/ErrorMessage>/);
  if (errMatch) throw new Error(`IBKR error ${errMatch[1]}: ${errMatch[2]}`);

  // Check if still processing
  if (xmlText.includes('Statement generation in progress')) throw new Error('Report still generating');

  // Find trade elements via regex
  const tagNames = ['Trade', 'Order', 'Execution', 'TradeConfirm'];
  let matches = [];
  for (const tag of tagNames) {
    const re = new RegExp(`<${tag}\\s+([^>]+)\\/>`, 'g');
    let m;
    while ((m = re.exec(xmlText)) !== null) matches.push(m[1]);
    if (matches.length) break;
  }

  // Parse attributes from each match
  const optionTrades = [];
  for (const attrStr of matches) {
    const attr = (names) => {
      for (const n of names) {
        const re = new RegExp(`${n}="([^"]*)"`, 'i');
        const m = attrStr.match(re);
        if (m) return m[1];
      }
      return null;
    };

    const cat = attr(['assetCategory', 'assetType', 'secType']);
    if (cat && cat !== 'OPT' && cat !== 'FOP' && !cat.includes('Option')) continue;

    const symbol = attr(['symbol', 'description']) || '';
    const ticker = attr(['underlyingSymbol', 'underlying']) || symbol.split(/\s/)[0];
    const expiry = attr(['expiry', 'expirationDate', 'lastTradeDateOrContractMonth']);
    const strike = parseFloat(attr(['strike', 'strikePrice']) || 'NaN');
    const side = attr(['putCall', 'right', 'putOrCall']);
    const qty = parseInt((attr(['quantity', 'filledQuantity', 'tradeQuantity']) || '0').replace(/,/g, ''));
    const price = parseFloat(attr(['tradePrice', 'price', 'avgPrice']) || '0');
    const proceeds = parseFloat(attr(['proceeds', 'netCash']) || '0');
    const commission = parseFloat(attr(['ibCommission', 'commission']) || '0');
    const code = attr(['openCloseIndicator', 'openClose', 'side']) || '';
    const rawDt = attr(['dateTime', 'tradeDate', 'date']) || '';

    if (!cat && (isNaN(strike) || !expiry || !side)) continue;

    let expiryFmt = expiry;
    if (expiry && !expiry.includes('-') && expiry.length === 8) {
      expiryFmt = expiry.slice(0, 4) + '-' + expiry.slice(4, 6) + '-' + expiry.slice(6, 8);
    }
    const dateOnly = rawDt.split(/[;T,]/)[0].trim().replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');

    if (!ticker || !expiryFmt || isNaN(strike) || isNaN(qty) || qty === 0) continue;

    optionTrades.push({
      ticker, expiry: expiryFmt, strike, side: (side || '').charAt(0).toUpperCase(),
      datetime: rawDt, qty, price, proceeds, commission, code,
      dateOnly,
      isOpen: code === 'O',
      isClose: code === 'C',
    });
  }
  return optionTrades;
}

function pairSpreads(optionTrades) {
  const opens = optionTrades.filter(t => t.isOpen);
  const closes = optionTrades.filter(t => t.isClose);
  const groups = {};
  for (const t of opens) {
    const key = t.ticker + '|' + t.expiry + '|' + t.datetime;
    if (!groups[key]) groups[key] = [];
    groups[key].push(t);
  }

  const spreads = [];
  for (const [, legs] of Object.entries(groups)) {
    if (legs.length !== 2) continue;
    const shortLeg = legs.find(l => l.qty < 0);
    const longLeg = legs.find(l => l.qty > 0);
    if (!shortLeg || !longLeg) continue;
    if (shortLeg.side !== longLeg.side) continue;
    if (Math.abs(shortLeg.qty) !== Math.abs(longLeg.qty)) continue;

    const contracts = Math.abs(shortLeg.qty);
    const isPut = shortLeg.side === 'P';
    const shortStrike = shortLeg.strike;
    const longStrike = longLeg.strike;
    const premium = Math.round(Math.abs(shortLeg.price - longLeg.price) * contracts * 100);
    const entryDate = shortLeg.dateOnly;

    let tradeType;
    if (isPut && shortStrike > longStrike) tradeType = 'Bull Put Spread';
    else if (isPut && shortStrike < longStrike) tradeType = 'Bear Put Spread';
    else if (!isPut && shortStrike < longStrike) tradeType = 'Bull Call Spread';
    else tradeType = 'Bear Call Spread';

    // Check for closes
    const matchingCloses = closes.filter(c => c.ticker === shortLeg.ticker && c.expiry === shortLeg.expiry && (c.strike === shortStrike || c.strike === longStrike));
    const shortCloses = matchingCloses.filter(c => c.strike === shortStrike);
    const longCloses = matchingCloses.filter(c => c.strike === longStrike);

    let exitDate = null, status = 'Open', realizedPnl = null;
    let totalComm = Math.abs(shortLeg.commission) + Math.abs(longLeg.commission);

    if (shortCloses.length > 0 && longCloses.length > 0) {
      status = 'Closed';
      exitDate = shortCloses[shortCloses.length - 1].dateOnly;
      const closeCost = Math.abs(shortCloses.reduce((s, c) => s + c.proceeds, 0) + longCloses.reduce((s, c) => s + c.proceeds, 0));
      const closeComm = shortCloses.reduce((s, c) => s + Math.abs(c.commission), 0) + longCloses.reduce((s, c) => s + Math.abs(c.commission), 0);
      totalComm += closeComm;
      realizedPnl = Math.round((premium - closeCost - totalComm) * 100) / 100;
    }

    const dte = Math.round((new Date(shortLeg.expiry) - new Date(entryDate)) / 86400000);

    spreads.push({
      ticker: shortLeg.ticker, tradeType, status, entryDate,
      expiry: shortLeg.expiry, shortStrike, longStrike,
      premiumCollected: premium, contracts, realizedPnl, exitDate,
      dteAtEntry: dte,
      entryType: '', thesis: '', exitReason: '',
      thesisAccuracy: '', lessonLearned: '',
      chartLink: '', tpPrice: '', slPrice: '',
      rolls: [], journal: []
    });
  }
  return spreads.sort((a, b) => a.entryDate.localeCompare(b.entryDate));
}

// ── Daily Brief + Post-Session Journal ────────────────────────────────────

async function sendDailyBrief() {
  try {
    const state = await loadState();
    const trades = (state.trades || []).filter(t => t.status === 'Open');
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dateLabel = `${dayNames[et.getDay()]} ${monthNames[et.getMonth()]} ${et.getDate()}`;
    const today = formatDate(et);

    let msg = `☀️ <b>DAILY BRIEF — ${dateLabel}</b>\n\n`;

    // Open positions
    if (trades.length) {
      msg += `📊 <b>OPEN POSITIONS (${trades.length})</b>\n`;
      for (const t of trades) {
        const dl = daysLeft(t.expiry);
        const slStr = t.slPrice ? `SL $${t.slPrice}` : '';
        const tpStr = t.tpPrice ? `TP $${t.tpPrice}` : '';
        const flags = [];
        if (slStr) flags.push(slStr);
        if (tpStr) flags.push(tpStr);
        const flagStr = flags.length ? ` · ${flags.join(' · ')}` : '';
        const dteWarn = dl <= 0 ? ' ⚠️ EXPIRED' : dl <= 1 ? ' ⚠️ EXPIRING' : '';
        msg += `  ${t.ticker} ${t.shortStrike}/${t.longStrike} · ${dl}d${dteWarn}${flagStr}\n`;
      }
      msg += '\n';
    } else {
      msg += `📊 No open positions\n\n`;
    }

    // Account
    const bal = state.accountBalance || 0;
    const deps = state.ytdDeposits || 0;
    const closedTrades = (state.trades || []).filter(t => t.status !== 'Open' && t.realizedPnl != null);
    const ytdPnl = closedTrades.reduce((s, t) => s + (t.realizedPnl || 0), 0);
    msg += `💰 Account: $${bal.toLocaleString()} · YTD: ${ytdPnl >= 0 ? '+' : '-'}$${Math.abs(ytdPnl).toFixed(0)}\n`;

    // Portfolio risk
    if (trades.length) {
      const totalRisk = trades.reduce((s, t) => {
        const w = Math.abs(t.shortStrike - t.longStrike);
        return s + (w * 100 * (t.contracts || 1)) - (t.premiumCollected || 0);
      }, 0);
      const riskPct = bal > 0 ? (totalRisk / bal * 100).toFixed(0) : '?';
      msg += `📈 Portfolio risk: $${totalRisk.toLocaleString()} (${riskPct}%)\n`;
    }

    // Watchlist targets
    const targets = state.watchlistTargets || {};
    const activeTargets = Object.entries(targets).filter(([, v]) => v && v.price);
    if (activeTargets.length) {
      const targetStrs = activeTargets.map(([t, v]) => `${t} @ $${v.price}`).join(', ');
      msg += `🔔 Watchlist targets: ${targetStrs}\n`;
    }
    msg += '\n';

    // Today's flags
    const flags = [];
    for (const t of trades) {
      const dl = daysLeft(t.expiry);
      if (dl <= 0) flags.push(`⚠️ ${t.ticker} expired — manage or close`);
      else if (dl === 1) flags.push(`⚠️ ${t.ticker} expires today`);
    }
    if (flags.length) {
      msg += `⚡ <b>TODAY'S FLAGS:</b>\n`;
      for (const f of flags) msg += `  ${f}\n`;
      msg += '\n';
    }

    msg += `Focus up. Stick to the plan. 🎯`;

    await tg(msg);

    // Save morning brief text to dailyJournal
    if (!state.dailyJournal) state.dailyJournal = {};
    if (!state.dailyJournal[today]) state.dailyJournal[today] = {};
    state.dailyJournal[today].morningBrief = msg.replace(/<[^>]+>/g, ''); // strip HTML
    await saveState(state);

    console.log('[BRIEF] Daily brief sent');
  } catch (e) {
    console.error('[BRIEF]', e.message);
  }
}

async function sendEODPrompt() {
  try {
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dateLabel = `${dayNames[et.getDay()]} ${monthNames[et.getMonth()]} ${et.getDate()}`;
    const today = formatDate(et);

    await tg(
      `📝 <b>POST-SESSION JOURNAL — ${dateLabel}</b>\n\n` +
      `How was today? Reply with your thoughts:\n` +
      `• What happened in the market?\n` +
      `• Any trades you took — why?\n` +
      `• What did you see on Daily Show / Spectra?\n` +
      `• Anything you'd do differently?\n\n` +
      `Just reply naturally — I'll save it as today's journal entry.`
    );

    pendingJournal = { date: today, timestamp: Date.now() };
    console.log('[JOURNAL] EOD prompt sent, waiting for reply');
  } catch (e) {
    console.error('[JOURNAL]', e.message);
  }
}

// ── Schedule ────────────────────────────────────────────────────────────────

// Stock + alert check every 60s during market hours
cron.schedule('* * * * 1-5', () => {
  if (isMarketHours()) runCheck();
}, { timezone: 'America/New_York' });

// Market open at 9:30 AM ET
cron.schedule('30 9 * * 1-5', sendMarketOpen, { timezone: 'America/New_York' });

// Hourly summaries at :00 during market hours (10 AM - 3 PM)
cron.schedule('0 10-15 * * 1-5', () => {
  if (isMarketHours()) sendHourlySummary();
}, { timezone: 'America/New_York' });

// Market close summary at 4:05 PM ET
cron.schedule('5 16 * * 1-5', sendMarketClose, { timezone: 'America/New_York' });

// Daily Brief at 7:00 AM ET
cron.schedule('0 7 * * 1-5', sendDailyBrief, { timezone: 'America/New_York' });

// Auto IBKR Sync at 5:00 PM ET (after close, before journal prompt)
cron.schedule('0 17 * * 1-5', autoSyncIBKR, { timezone: 'America/New_York' });

// Post-Session Journal Prompt at 6:00 PM ET
cron.schedule('0 18 * * 1-5', sendEODPrompt, { timezone: 'America/New_York' });

// ── Startup ─────────────────────────────────────────────────────────────────
console.log('═══════════════════════════════════════════════════════════');
console.log('  Trading Desk Alert Server v2');
console.log('  Stock checks: every 60s | Spread checks: every 5 min');
console.log('  Telegram commands: /open /close /status /pnl /sl /ssl /tp /brief /journal');
console.log('  Hourly summaries: 10 AM - 3 PM ET');
console.log('  Daily brief: 7:00 AM ET | Journal prompt: 6:00 PM ET');
console.log('═══════════════════════════════════════════════════════════');

// Start Telegram command listener (runs 24/7)
telegramLoop();

// Run initial check
if (isMarketHours()) {
  console.log('Market is open — running initial check...');
  runCheck();
} else {
  console.log('Market closed — waiting for next market hours...');
  loadState()
    .then(async s => {
      const openTrades = (s.trades || []).filter(t => t.status === 'Open');
      console.log(`✓ Supabase connected — ${openTrades.length} open positions`);
      console.log('✓ Telegram configured');
      console.log('✓ MarketData.app Trader plan active');

      // Test spread fetch on startup
      if (openTrades.length > 0) {
        console.log('Testing spread fetch...');
        try {
          const sv = await fetchSpreadValue(openTrades[0]);
          if (sv) console.log(`✓ Spread test: ${openTrades[0].ticker} = $${sv.mid.toFixed(2)}`);
          else console.log('⚠ Spread test returned null');
        } catch (e) {
          console.error('✗ Spread test failed:', e.message);
        }
      }
    })
    .catch(e => console.error('✗ Startup check failed:', e.message));
}
