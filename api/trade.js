// Auto-exit trade endpoint — places close orders via IBKR Client Portal Gateway
// Called by the app when TP/SL triggers fire

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Trade-Auth');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });

  // Auth check
  const authToken = req.headers['x-trade-auth'];
  if (!authToken || authToken !== process.env.TRADE_AUTH_TOKEN) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const GW = process.env.IBKR_GATEWAY_URL; // e.g., https://your-vps.com:5000/v1/api
  if (!GW) return res.status(500).json({ error: 'IBKR gateway not configured' });

  const { action, ticker, expiry, shortStrike, longStrike, contracts, side, tradeId, reason } = req.body;

  if (action !== 'close') return res.status(400).json({ error: 'Only close action supported' });
  if (!ticker || !expiry || !shortStrike || !longStrike || !contracts || !side) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  try {
    // Step 1: Get account ID
    const acctResp = await gw(GW, '/portfolio/accounts');
    const accountId = acctResp?.[0]?.accountId;
    if (!accountId) throw new Error('No IBKR account found — is gateway authenticated?');

    // Step 2: Get current positions to find the exact conids
    const positions = await gw(GW, `/portfolio/${accountId}/positions/0`);

    // Find the two option legs matching this spread
    const isPut = side.toUpperCase() === 'P' || side.toLowerCase() === 'put';
    const right = isPut ? 'P' : 'C';

    // Format expiry for matching: YYYY-MM-DD → YYYYMMDD
    const expiryClean = expiry.replace(/-/g, '');

    const shortLeg = positions.find(p =>
      p.ticker?.includes(ticker) &&
      p.putOrCall === right &&
      Math.abs(p.strike - shortStrike) < 0.01 &&
      p.expiry === expiryClean &&
      p.position < 0 // short position
    );

    const longLeg = positions.find(p =>
      p.ticker?.includes(ticker) &&
      p.putOrCall === right &&
      Math.abs(p.strike - longStrike) < 0.01 &&
      p.expiry === expiryClean &&
      p.position > 0 // long position
    );

    if (!shortLeg || !longLeg) {
      // Try alternative matching via underlying symbol
      const altShort = positions.find(p =>
        (p.underlyingSymbol === ticker || p.contractDesc?.includes(ticker)) &&
        p.putOrCall === right &&
        Math.abs(p.strike - shortStrike) < 0.01 &&
        p.position < 0
      );
      const altLong = positions.find(p =>
        (p.underlyingSymbol === ticker || p.contractDesc?.includes(ticker)) &&
        p.putOrCall === right &&
        Math.abs(p.strike - longStrike) < 0.01 &&
        p.position > 0
      );

      if (!altShort || !altLong) {
        return res.status(404).json({
          error: 'Position not found in IBKR',
          detail: `Could not find ${ticker} ${longStrike}/${shortStrike} ${right} ${expiry}`,
          positionCount: positions.length
        });
      }

      // Use alternative matches
      return await placeCloseOrders(GW, accountId, altShort, altLong, contracts, reason, res);
    }

    return await placeCloseOrders(GW, accountId, shortLeg, longLeg, contracts, reason, res);

  } catch (e) {
    console.error('Trade error:', e);
    res.status(500).json({ error: e.message });
  }
}

async function placeCloseOrders(GW, accountId, shortLeg, longLeg, contracts, reason, res) {
  const qty = Math.abs(contracts);

  // Step 3: Get current quotes for the legs to set limit prices
  let shortQuote, longQuote;
  try {
    // Snapshot market data for both legs
    const snapShort = await gw(GW, `/iserver/marketdata/snapshot?conids=${shortLeg.conid}&fields=84,86`);
    const snapLong = await gw(GW, `/iserver/marketdata/snapshot?conids=${longLeg.conid}&fields=84,86`);

    // Fields: 84 = bid, 86 = ask
    shortQuote = { bid: parseFloat(snapShort?.[0]?.['84']) || 0, ask: parseFloat(snapShort?.[0]?.['86']) || 0 };
    longQuote = { bid: parseFloat(snapLong?.[0]?.['84']) || 0, ask: parseFloat(snapLong?.[0]?.['86']) || 0 };
  } catch {
    // If snapshot fails, use adaptive market order
    shortQuote = null;
    longQuote = null;
  }

  // Step 4: Place close orders for each leg
  // Short leg: BUY to close (pay the ask, or use mid for limit)
  // Long leg: SELL to close (receive the bid, or use mid for limit)
  const orders = [];

  if (shortQuote && longQuote && shortQuote.ask > 0) {
    // Use adaptive limit orders at mid price
    const shortMid = (shortQuote.bid + shortQuote.ask) / 2;
    const longMid = (longQuote.bid + longQuote.ask) / 2;

    orders.push({
      conid: shortLeg.conid,
      orderType: 'LMT',
      side: 'BUY',
      quantity: qty,
      price: Math.round(shortMid * 100) / 100, // round to cents
      tif: 'DAY',
      referrer: 'TradeDeskAutoExit'
    });
    orders.push({
      conid: longLeg.conid,
      orderType: 'LMT',
      side: 'SELL',
      quantity: qty,
      price: Math.round(longMid * 100) / 100,
      tif: 'DAY',
      referrer: 'TradeDeskAutoExit'
    });
  } else {
    // Fallback: market orders
    orders.push({
      conid: shortLeg.conid,
      orderType: 'MKT',
      side: 'BUY',
      quantity: qty,
      tif: 'DAY',
      referrer: 'TradeDeskAutoExit'
    });
    orders.push({
      conid: longLeg.conid,
      orderType: 'MKT',
      side: 'SELL',
      quantity: qty,
      tif: 'DAY',
      referrer: 'TradeDeskAutoExit'
    });
  }

  // Place orders
  const results = [];
  for (const order of orders) {
    const orderResp = await gw(GW, `/iserver/account/${accountId}/orders`, 'POST', { orders: [order] });

    // Handle order confirmation prompts (IBKR sometimes asks "are you sure?")
    if (orderResp?.[0]?.id) {
      // Needs confirmation — auto-confirm
      const confirmResp = await gw(GW, `/iserver/reply/${orderResp[0].id}`, 'POST', { confirmed: true });
      results.push(confirmResp);
    } else {
      results.push(orderResp);
    }
  }

  res.status(200).json({
    success: true,
    reason,
    shortLeg: { conid: shortLeg.conid, strike: shortLeg.strike, action: 'BUY_TO_CLOSE' },
    longLeg: { conid: longLeg.conid, strike: longLeg.strike, action: 'SELL_TO_CLOSE' },
    orders: results
  });
}

// Helper: call IBKR gateway
async function gw(base, path, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    // IBKR gateway uses self-signed certs
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(`${base}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`IBKR Gateway ${resp.status}: ${text}`);
  }
  return resp.json();
}
