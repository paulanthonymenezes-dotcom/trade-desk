const SUPABASE_URL='https://arjpswrirszerhpbojgs.supabase.co';
const SUPABASE_KEY='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFyanBzd3JpcnN6ZXJocGJvamdzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzMzgyOTQsImV4cCI6MjA4NzkxNDI5NH0.aLCb5xP8WbeQuMpLJ3uoGFYebENCWQ-WBbtQZLvtYuA';

(async()=>{
  const r = await fetch(SUPABASE_URL+'/rest/v1/state?id=eq.main&select=data',{headers:{apikey:SUPABASE_KEY,Authorization:'Bearer '+SUPABASE_KEY}});
  const rows = await r.json();
  const data = rows[0].data;

  const existingIds = new Set(data.trades.map(t=>t.id));

  const missingTrades = [
    {id:24,pop:'',side:'P',rolls:[],expiry:'2026-03-13',sector:'Communication Services',status:'Closed',thesis:'WBD deal fell apart, EPS and revenue beat. Expected return to $100+ range.',ticker:'NFLX',journal:[],slLevel:'custom',slPrice:'85',tpLevel:'custom',tpPrice:'105',_autoMAE:{pnl:-990,src:'live',date:'3/11/2026',price:96.915},_autoMFE:{pnl:139.99999999999886,src:'live',date:'2026-03-11',price:95.925},exitDate:'2026-03-10',spreadSL:'1.00',spreadTP:'0.17',contracts:20,entryDate:'2026-03-09',tradeType:'Bull Put Spread',longStrike:100,realizedPnl:-408.08,shortStrike:101,spreadSLPct:'100',spreadTPPct:'75',premiumCollected:1340},
    {id:25,ticker:'IBIT',status:'Open',tradeType:'Bull Put Spread',shortStrike:47,longStrike:41,contracts:10,premiumCollected:2280,entryDate:'2026-03-10',expiry:'2026-03-21',sector:'Crypto',side:'P',spreadSL:'3.72',spreadTP:'0.57',slPrice:'35',tpPrice:'48',thesis:'Bitcoin correction overdone, IBIT holding 38 support',journal:[],rolls:[]},
    {id:26,ticker:'MSFT',status:'Open',tradeType:'Bull Put Spread',shortStrike:420,longStrike:400,contracts:2,premiumCollected:2400,entryDate:'2026-03-10',expiry:'2026-03-21',sector:'Technology',side:'P',spreadSL:'15.60',spreadTP:'3.00',slPrice:'370',tpPrice:'420',thesis:'Cloud AI momentum, oversold on market pullback',journal:[],rolls:[]},
    {id:27,ticker:'IBIT',status:'Open',tradeType:'Bull Put Spread',shortStrike:51,longStrike:38,contracts:5,premiumCollected:4650,entryDate:'2026-03-10',expiry:'2026-04-17',sector:'Crypto',side:'P',spreadSL:'12.07',spreadTP:'2.33',slPrice:'30',tpPrice:'52',thesis:'Longer-dated Bitcoin recovery play',journal:[],rolls:[]}
  ];

  for (const t of missingTrades) {
    if (!existingIds.has(t.id)) {
      data.trades.push(t);
      console.log('Restored trade #'+t.id+' '+t.ticker);
    } else {
      console.log('Trade #'+t.id+' already exists');
    }
  }

  const maxId = data.trades.reduce((m,t)=>Math.max(m,t.id||0),0);
  data.nextId = maxId + 1;
  console.log('nextId set to', data.nextId);

  const w = await fetch(SUPABASE_URL+'/rest/v1/state', {
    method:'POST',
    headers:{'Content-Type':'application/json',apikey:SUPABASE_KEY,Authorization:'Bearer '+SUPABASE_KEY,Prefer:'resolution=merge-duplicates,return=minimal'},
    body: JSON.stringify({id:'main',data})
  });
  console.log('Write status:', w.status);
  console.log('Total trades:', data.trades.length);
  console.log('Open:', data.trades.filter(t=>t.status==='Open').map(t=>'#'+t.id+' '+t.ticker).join(', '));
})();
