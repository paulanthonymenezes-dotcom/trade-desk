export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { symbols } = req.query;
  if (!symbols) return res.status(400).json({ error: 'Missing symbols parameter' });

  const tickers = symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean).slice(0, 30);

  try {
    const results = {};
    await Promise.all(tickers.map(async ticker => {
      try {
        const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?interval=1d&range=1d&includePrePost=false`;
        const r = await fetch(url, {
          headers: { 'User-Agent': 'Mozilla/5.0 (compatible; TradingDesk/1.0)' }
        });
        if (!r.ok) return;
        const data = await r.json();
        const meta = data.chart?.result?.[0]?.meta;
        if (!meta || !meta.regularMarketPrice) return;

        const prevClose = meta.chartPreviousClose || meta.previousClose;
        const price = meta.regularMarketPrice;
        const change = price - prevClose;
        const changePct = prevClose ? (change / prevClose) * 100 : 0;

        results[ticker] = {
          price,
          change,
          changePct,
          previousClose: prevClose,
          volume: meta.regularMarketVolume || 0,
          updated: meta.regularMarketTime || 0,
          currency: meta.currency || 'USD'
        };
      } catch {}
    }));

    res.status(200).json({ s: 'ok', quotes: results });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
