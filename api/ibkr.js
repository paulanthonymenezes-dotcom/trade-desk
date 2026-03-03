export default async function handler(req, res) {
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { url } = req.query;
  if (!url) return res.status(400).json({ error: 'Missing url parameter' });

  // Only allow IBKR domains
  try {
    const parsed = new URL(url);
    if (!parsed.hostname.endsWith('interactivebrokers.com')) {
      return res.status(403).json({ error: 'Only interactivebrokers.com URLs allowed' });
    }
  } catch {
    return res.status(400).json({ error: 'Invalid URL' });
  }

  try {
    const response = await fetch(url);
    const text = await response.text();
    const ct = response.headers.get('Content-Type') || 'text/xml';
    res.setHeader('Content-Type', ct);
    res.status(200).send(text);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
