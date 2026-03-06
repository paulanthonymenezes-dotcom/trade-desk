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
        volume: meta.regularMarketVolume || 0
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

    // ── Spread stop loss (911 — non-negotiable) ─────────────────────────

    if (t.spreadSL && spread?.mid != null && spread.mid >= parseFloat(t.spreadSL) && !alertsSent.has(key + 'spreadSL')) {
      alertsSent.add(key + 'spreadSL');
      const premium = t.premiumCollected || 0;
      const contracts = t.contracts || 1;
      const costToClose = spread.mid * 100 * contracts;
      const pnl = premium - costToClose;
      tg(
        `🚨🚨🚨 <b>${t.ticker} SPREAD STOP LOSS — GET OUT NOW</b>\n\n` +
        `Spread: $${spread.mid.toFixed(2)} ≥ SSL $${t.spreadSL}\n` +
        `P&L: -$${Math.abs(pnl).toFixed(0)}\n` +
        `${t.shortStrike}/${t.longStrike} x${contracts} | DTE: ${dl}\n\n` +
        `<b>THIS IS YOUR HARD STOP. CLOSE IT.</b>`
      );
      alertCount++;
    }

    // ── Percentage-based spread alerts ───────────────────────────────────

    if (pnlData && t.premiumCollected) {
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
      if (!msg?.text || String(msg.chat?.id) !== TG_CHAT) continue;

      const text = msg.text.trim();
      if (text.startsWith('/')) {
        await handleCommand(text);
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
      case '/ssl': return await cmdUpdateField(parts, 'spreadSL', 'Spread Stop Loss');
      case '/tp': return await cmdUpdateField(parts, 'tpPrice', 'Take Profit');
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
    `/status — All positions overview\n` +
    `/spreads — Live spread values\n` +
    `/pnl — P&L summary\n` +
    `/sl TICKER PRICE — Price stop loss\n` +
    `/ssl TICKER VALUE — Spread stop loss (911)\n` +
    `/tp TICKER PRICE — Take profit\n` +
    `/{ticker} — Detail on one position\n\n` +
    `<i>/sl NVDA 192 — thesis broken, get out</i>\n` +
    `<i>/ssl NVDA 4.50 — spread can't go above this</i>`
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
    const prox = q ? `${((q.price - t.shortStrike) / q.price * 100).toFixed(1)}%` : '?';
    const pnlData = unrealizedPnl(t, q?.price);
    const pnlUp = pnlData ? pnlData.pnl >= 0 : true;
    const status = !q ? '⚪' : pnlData ? (pnlUp ? '🟢' : '🔴') : (q.price < t.shortStrike ? '🔴' : '🟢');
    const spreadStr = spread ? `$${spread.mid.toFixed(2)}` : '-';
    const pnlStr = pnlData ? `${pnlData.pnl >= 0 ? '+' : '-'}$${Math.abs(pnlData.pnl).toFixed(0)}` : '';

    // Distance to SL/TP
    const slDist = (t.slPrice && q) ? `SL:$${t.slPrice} (${(q.price - parseFloat(t.slPrice)).toFixed(1)} away)` : '';
    const sslDist = (t.spreadSL && spread) ? `SSL:$${t.spreadSL} ($${(parseFloat(t.spreadSL) - spread.mid).toFixed(2)} away)` : '';
    const tpDist = (t.tpPrice && q) ? `TP:$${t.tpPrice} (${(parseFloat(t.tpPrice) - q.price).toFixed(1)} away)` : '';

    // Daily implied move from IV
    const impliedMove = (spread?.iv && q) ? `±$${(q.price * spread.iv * Math.sqrt(1/252)).toFixed(2)}/day` : '';

    let line = `${status} <b>${t.ticker}</b> ${price} (${chg})`;
    line += `\n   ${t.shortStrike}/${t.longStrike} x${t.contracts} | ${dl}DTE`;
    line += `\n   Spread: ${spreadStr} | P&L: ${pnlStr} | Prox: ${prox}`;
    if (impliedMove) line += `\n   IV Move: ${impliedMove}`;
    if (slDist || sslDist || tpDist) line += `\n   ${[slDist, sslDist, tpDist].filter(Boolean).join('\n   ')}`;

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
    if (t.spreadSL) msg += `\n🚨 Spread SL: $${t.spreadSL}`;
    if (t.tpPrice) msg += `\n✅ TP: $${t.tpPrice}`;

    msg += `\n\n<i>/sl ${ticker} PRICE — price stop\n/ssl ${ticker} VALUE — spread stop (911)\n/tp ${ticker} PRICE — take profit</i>`;

    tg(msg);
  }
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
      const prevSpread = prevSpreadCache[t.id];
      const prevPrice = prevQuoteCache[t.ticker];
      const pnlData = unrealizedPnl(t, q.price);
      const icon = pnlData ? (pnlData.pnl >= 0 ? '🟢' : '🔴') : '⚪';

      // Price change since last hour
      const priceChg = prevPrice ? q.price - prevPrice : null;
      const priceChgStr = priceChg != null ? ` (${priceChg >= 0 ? '↑' : '↓'}$${Math.abs(priceChg).toFixed(2)}/hr)` : '';

      // Spread change since last hour
      const spreadChg = (spread && prevSpread) ? spread.mid - prevSpread.mid : null;
      const spreadChgStr = spreadChg != null ? ` (${spreadChg > 0 ? '↑' : spreadChg < 0 ? '↓' : '→'}$${Math.abs(spreadChg).toFixed(2)})` : '';

      let line = `${icon} <b>${t.ticker}</b>${priceChgStr}`;

      // Spread + change
      if (spread) line += `\n   Sprd: $${spread.mid.toFixed(2)}${spreadChgStr}`;

      // Distance to price SL
      if (t.slPrice) {
        const slDist = q.price - parseFloat(t.slPrice);
        const impliedMove = spread?.iv ? q.price * spread.iv * Math.sqrt(1/252) : null;
        const slMoves = impliedMove ? ` (${(slDist / impliedMove).toFixed(1)}x IV move)` : '';
        line += `\n   SL: $${slDist.toFixed(1)} away${slMoves}`;
      }

      // Distance to spread SL (911)
      if (t.spreadSL && spread) {
        const sslDist = parseFloat(t.spreadSL) - spread.mid;
        line += `\n   SSL: $${sslDist.toFixed(2)} from hard stop`;
      }

      // Distance to TP
      if (t.tpPrice) {
        const tpDist = parseFloat(t.tpPrice) - q.price;
        line += `\n   TP: $${tpDist.toFixed(1)} away`;
      }

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

// ── Startup ─────────────────────────────────────────────────────────────────
console.log('═══════════════════════════════════════════════════════════');
console.log('  Trading Desk Alert Server v2');
console.log('  Stock checks: every 60s | Spread checks: every 5 min');
console.log('  Telegram commands: /help /status /spreads /pnl /sl /tp');
console.log('  Hourly summaries: 10 AM - 3 PM ET');
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
