export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { ticker, start, end, interval = '1d' } = req.query;
  if (!ticker) return res.status(400).json({ s: 'error', errmsg: 'Missing ticker' });

  try {
    // Build Yahoo Finance chart URL
    const params = new URLSearchParams({ interval, includePrePost: 'false' });

    if (start && end) {
      // Convert YYYY-MM-DD to Unix timestamps
      params.set('period1', Math.floor(new Date(start + 'T00:00:00').getTime() / 1000));
      params.set('period2', Math.floor(new Date(end + 'T23:59:59').getTime() / 1000));
    } else {
      params.set('range', '1y');
    }

    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?${params}`;
    const r = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; TradingDesk/1.0)' }
    });
    if (!r.ok) return res.status(r.status).json({ s: 'error', errmsg: 'Yahoo HTTP ' + r.status });

    const data = await r.json();
    const result = data.chart?.result?.[0];
    if (!result || !result.timestamp) return res.status(200).json({ s: 'no_data' });

    const q = result.indicators?.quote?.[0];
    if (!q) return res.status(200).json({ s: 'no_data' });

    // Return MDA-compatible format: {s, t, o, h, l, c, v}
    res.status(200).json({
      s: 'ok',
      t: result.timestamp,
      o: q.open,
      h: q.high,
      l: q.low,
      c: q.close,
      v: q.volume,
    });
  } catch (e) {
    res.status(500).json({ s: 'error', errmsg: e.message });
  }
}
