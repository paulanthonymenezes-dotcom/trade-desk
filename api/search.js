export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { q } = req.query;
  if (!q || q.trim().length < 1) return res.status(400).json({ error: 'Missing q parameter' });

  try {
    const url = `https://query2.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(q.trim())}&quotesCount=8&newsCount=0&enableFuzzyQuery=false&quotesQueryId=tss_match_phrase_query`;
    const r = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; TradingDesk/1.0)' }
    });
    if (!r.ok) throw new Error('Yahoo search HTTP ' + r.status);
    const data = await r.json();

    const results = (data.quotes || [])
      .filter(q => q.quoteType === 'EQUITY' || q.quoteType === 'ETF')
      .slice(0, 8)
      .map(q => ({
        symbol: q.symbol,
        name: q.shortname || q.longname || '',
        exchange: q.exchDisp || q.exchange || '',
        type: q.quoteType
      }));

    res.status(200).json({ results });
  } catch (e) {
    res.status(500).json({ error: e.message, results: [] });
  }
}
