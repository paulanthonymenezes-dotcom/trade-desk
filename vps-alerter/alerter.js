#!/usr/bin/env node
// ── Trading Desk Alert Server ────────────────────────────────────────────────
// Runs on VPS, monitors positions via Supabase, sends Telegram alerts.
// No browser required — works 24/7 even when your Mac is off.
// ─────────────────────────────────────────────────────────────────────────────

const cron = require('node-cron');

// ── Config (Supabase anon key is public — same as frontend) ─────────────────
const SUPABASE_URL = "https://arjpswrirszerhpbojgs.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFyanBzd3JpcnN6ZXJocGJvamdzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzMzgyOTQsImV4cCI6MjA4NzkxNDI5NH0.aLCb5xP8WbeQuMpLJ3uoGFYebENCWQ-WBbtQZLvtYuA";

// ── State ───────────────────────────────────────────────────────────────────
const alertsSent = new Set();   // dedup alerts within a trading day
let lastMarketDate = null;      // track day for resetting alerts
let lastState = null;           // cached state for logging
let consecutiveErrors = 0;      // track errors for alerting

// ── Telegram ────────────────────────────────────────────────────────────────
async function sendTelegram(botToken, chatId, msg) {
  if (!botToken || !chatId) {
    console.log('[SKIP] No Telegram config:', msg.replace(/<[^>]+>/g, ''));
    return;
  }
  try {
    const r = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: msg, parse_mode: 'HTML' })
    });
    if (!r.ok) console.error('[TG] Send failed:', r.status);
  } catch (e) {
    console.error('[TG] Error:', e.message);
  }
}

// ── Load state from Supabase ────────────────────────────────────────────────
async function loadState() {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/state?id=eq.main&select=data`, {
    headers: {
      'apikey': SUPABASE_KEY,
      'Authorization': `Bearer ${SUPABASE_KEY}`
    }
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${await r.text()}`);
  const rows = await r.json();
  if (!rows[0]?.data) throw new Error('No state found in Supabase');
  return rows[0].data;
}

// ── Fetch quotes from Yahoo Finance ─────────────────────────────────────────
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

// ── Days until expiry ───────────────────────────────────────────────────────
function daysLeft(expiry) {
  if (!expiry) return Infinity;
  const exp = new Date(expiry + 'T16:00:00-05:00');
  return Math.ceil((exp - new Date()) / 86400000);
}

// ── Check alerts ────────────────────────────────────────────────────────────
function checkAlerts(state, quotes) {
  const trades = (state.trades || []).filter(t => t.status === 'Open');
  const strikeProx = state.alertStrikeProx || 5;
  const tgBot = state.tgBot;
  const tgChat = state.tgChat;
  let alertCount = 0;

  for (const t of trades) {
    const q = quotes[t.ticker];
    if (!q) continue;

    const prox = ((q.price - t.shortStrike) / q.price) * 100;
    const dl = daysLeft(t.expiry);
    const key = t.id + '-';

    // Strike proximity warning
    if (prox < strikeProx && prox >= 0 && !alertsSent.has(key + 'prox')) {
      alertsSent.add(key + 'prox');
      sendTelegram(tgBot, tgChat,
        `⚠️ <b>${t.ticker}</b> — $${q.price.toFixed(2)} only ${prox.toFixed(1)}% above short strike $${t.shortStrike}\nDTE: ${dl}`
      );
      alertCount++;
    }

    // Short strike breach
    if (q.price < t.shortStrike && !alertsSent.has(key + 'breach')) {
      alertsSent.add(key + 'breach');
      sendTelegram(tgBot, tgChat,
        `🔴 <b>${t.ticker} BREACHED SHORT STRIKE</b>\n$${q.price.toFixed(2)} < $${t.shortStrike} | DTE: ${dl}`
      );
      alertCount++;
    }

    // TP hit
    if (t.tpPrice && q.price >= parseFloat(t.tpPrice) && !alertsSent.has(key + 'tp')) {
      alertsSent.add(key + 'tp');
      sendTelegram(tgBot, tgChat,
        `✅ <b>${t.ticker} TAKE PROFIT HIT</b>\n$${q.price.toFixed(2)} ≥ TP $${t.tpPrice}\n${t.longStrike}/${t.shortStrike} x${t.contracts}`
      );
      alertCount++;
    }

    // SL hit
    if (t.slPrice && q.price <= parseFloat(t.slPrice) && !alertsSent.has(key + 'sl')) {
      alertsSent.add(key + 'sl');
      sendTelegram(tgBot, tgChat,
        `🛑 <b>${t.ticker} STOP LOSS HIT</b>\n$${q.price.toFixed(2)} ≤ SL $${t.slPrice}\n${t.longStrike}/${t.shortStrike} x${t.contracts}`
      );
      alertCount++;
    }

    // Expiry warning (1 DTE)
    if (dl === 1 && !alertsSent.has(key + 'exp')) {
      alertsSent.add(key + 'exp');
      sendTelegram(tgBot, tgChat,
        `⏰ <b>${t.ticker}</b> expires TOMORROW — ${t.longStrike}/${t.shortStrike} x${t.contracts}`
      );
      alertCount++;
    }
  }

  return alertCount;
}

// ── Market hours check (ET) ─────────────────────────────────────────────────
function isMarketHours() {
  const now = new Date();
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;

  const minutes = et.getHours() * 60 + et.getMinutes();
  // 9:25 AM (pre-open buffer) to 4:05 PM (post-close buffer)
  return minutes >= 565 && minutes <= 965;
}

function getETDate() {
  return new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' });
}

// ── Main check cycle ────────────────────────────────────────────────────────
async function runCheck() {
  // Reset alerts at start of each new trading day
  const today = getETDate();
  if (lastMarketDate && lastMarketDate !== today) {
    alertsSent.clear();
    console.log(`[${new Date().toISOString()}] New trading day — alerts reset`);
  }
  lastMarketDate = today;

  try {
    // Load latest state from Supabase
    const state = await loadState();
    lastState = state;

    const openTrades = (state.trades || []).filter(t => t.status === 'Open');
    if (openTrades.length === 0) {
      console.log(`[${new Date().toISOString()}] No open positions`);
      return;
    }

    // Get unique tickers
    const tickers = [...new Set(openTrades.map(t => t.ticker))];

    // Fetch quotes
    const quotes = await fetchQuotes(tickers);
    const quotedCount = Object.keys(quotes).length;

    // Check alerts
    const alertCount = checkAlerts(state, quotes);

    // Log
    const prices = tickers.map(t => `${t}:${quotes[t]?.price?.toFixed(2) || '?'}`).join(' ');
    console.log(`[${new Date().toISOString()}] ${quotedCount}/${tickers.length} quotes | ${alertCount} alerts | ${prices}`);

    consecutiveErrors = 0;
  } catch (e) {
    consecutiveErrors++;
    console.error(`[${new Date().toISOString()}] ERROR (${consecutiveErrors}x): ${e.message}`);

    // Alert on Telegram after 5 consecutive errors
    if (consecutiveErrors === 5 && lastState) {
      sendTelegram(lastState.tgBot, lastState.tgChat,
        `🚨 <b>ALERT SERVER ERROR</b>\n${e.message}\n5 consecutive failures — check VPS`
      );
    }
  }
}

// ── Market open/close notifications ─────────────────────────────────────────
async function sendMarketOpen() {
  try {
    const state = await loadState();
    lastState = state;
    const openTrades = (state.trades || []).filter(t => t.status === 'Open');
    if (openTrades.length === 0) return;

    const tickers = [...new Set(openTrades.map(t => t.ticker))];
    const tradesWithTP = openTrades.filter(t => t.tpPrice).length;
    const tradesWithSL = openTrades.filter(t => t.slPrice).length;

    // Reset alerts for new day
    alertsSent.clear();

    sendTelegram(state.tgBot, state.tgChat,
      `🟢 <b>Market Open — Alert Server Active</b>\n` +
      `Monitoring ${openTrades.length} positions (${tickers.length} tickers)\n` +
      `TP set: ${tradesWithTP} | SL set: ${tradesWithSL}\n` +
      `Checking every 60s until 4:00 PM ET`
    );
  } catch (e) {
    console.error('[OPEN]', e.message);
  }
}

async function sendMarketClose() {
  try {
    const state = await loadState();
    const openTrades = (state.trades || []).filter(t => t.status === 'Open');
    if (openTrades.length === 0) return;

    const quotes = await fetchQuotes([...new Set(openTrades.map(t => t.ticker))]);

    // Build summary
    const lines = openTrades.map(t => {
      const q = quotes[t.ticker];
      if (!q) return `  ${t.ticker}: no quote`;
      const prox = ((q.price - t.shortStrike) / q.price) * 100;
      const dl = daysLeft(t.expiry);
      const status = q.price < t.shortStrike ? '🔴' : prox < 5 ? '⚠️' : '🟢';
      return `  ${status} ${t.ticker} $${q.price.toFixed(2)} (${q.changePct >= 0 ? '+' : ''}${q.changePct.toFixed(1)}%) | ${prox.toFixed(1)}% from strike | ${dl} DTE`;
    });

    sendTelegram(state.tgBot, state.tgChat,
      `🔴 <b>Market Closed — Daily Summary</b>\n` +
      `${openTrades.length} open positions:\n` +
      lines.join('\n') + '\n' +
      `Alerts sent today: ${alertsSent.size}`
    );
  } catch (e) {
    console.error('[CLOSE]', e.message);
  }
}

// ── Schedule ────────────────────────────────────────────────────────────────

// Check every 60 seconds during market hours (Mon-Fri)
cron.schedule('* * * * 1-5', () => {
  if (isMarketHours()) runCheck();
}, { timezone: 'America/New_York' });

// Market open notification at 9:30 AM ET
cron.schedule('30 9 * * 1-5', sendMarketOpen, { timezone: 'America/New_York' });

// Market close summary at 4:05 PM ET
cron.schedule('5 16 * * 1-5', sendMarketClose, { timezone: 'America/New_York' });

// ── Startup ─────────────────────────────────────────────────────────────────
console.log('═══════════════════════════════════════════════════════');
console.log('  Trading Desk Alert Server');
console.log('  Checking every 60s during market hours (9:30-4:00 ET)');
console.log('  Reads positions from Supabase, sends alerts via Telegram');
console.log('═══════════════════════════════════════════════════════');

// Run an initial check if market is currently open
if (isMarketHours()) {
  console.log('Market is open — running initial check...');
  runCheck();
} else {
  console.log('Market closed — waiting for next market hours...');
  // Still do a state check to verify connectivity
  loadState()
    .then(s => {
      const openTrades = (s.trades || []).filter(t => t.status === 'Open');
      console.log(`✓ Supabase connected — ${openTrades.length} open positions`);
      if (s.tgBot && s.tgChat) console.log('✓ Telegram configured');
      else console.log('⚠ Telegram not configured — set bot token & chat ID in app Settings');
    })
    .catch(e => console.error('✗ Supabase connection failed:', e.message));
}
