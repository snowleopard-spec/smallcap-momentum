import { useState, useEffect, useRef } from "react";

// ═══════════════════════════════════════════════════════════
// UNICORN HUNT — Small Cap Momentum Screener
// Retro Synthwave × DeFi × MS-DOS Terminal
// ═══════════════════════════════════════════════════════════

const SIGNALS = [
  { key: "price_momentum", name: "Price Momentum", desc: "Composite 3/6/12 month returns, skipping most recent month to avoid reversal. Captures sustained upward trend.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "volume_surge", name: "Volume Surge", desc: "Recent volume vs 60-day average, normalised by market cap. Measures conviction — is real money flowing in?", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "price_acceleration", name: "Price Acceleration", desc: "Rate of change of momentum. Catches stocks early in their move before they appear on simple screens.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "rsi", name: "RSI Momentum", desc: "14-day Relative Strength Index, tuned for momentum. Sweet spot 60–80 scores highest, overextended >85 gets penalised.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "stochastic", name: "Stochastic", desc: "Slow Stochastic (14,3,3) — where price closed relative to its range. Blends level, crossover, and trend direction.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "financial_health", name: "Financial Health", desc: "Solvency, cash position, profitability, and filing recency from SEC 10-K/10-Q filings. Filters out distressed companies.", source: "SEC EDGAR XBRL", dataFile: "fundamentals" },
  { key: "news_attention", name: "News Attention", desc: "30-day article count and 7-day surge, direction-adjusted. For small caps, any media attention is meaningful.", source: "Polygon News API", dataFile: "news" },
  { key: "insider_activity", name: "Insider Buying", desc: "Form 4 insider purchases vs sales. Asymmetric: buying is strongly bullish, selling only mildly bearish.", source: "SEC EDGAR Form 4", dataFile: "insider" },
];

const DEFAULT_WEIGHTS = {
  price_momentum: 20, volume_surge: 12, price_acceleration: 12,
  rsi: 8, stochastic: 8, financial_health: 15,
  news_attention: 10, insider_activity: 15,
};

const MOCK_STATUS = {
  prices: { age: 0.2, stale: false },
  universe: { age: 0.2, stale: false },
  fundamentals: { age: 0.1, stale: false },
  news: { age: 0.1, stale: false },
  insider: { age: 0.1, stale: false },
};

const fmtCap = v => {
  if (!v) return "N/A";
  if (v >= 1e9) return `$${(v/1e9).toFixed(2)}B`;
  return `$${(v/1e6).toFixed(0)}M`;
};

const fmtVal = v => {
  if (!v && v !== 0) return "N/A";
  if (v >= 1e6) return `$${(v/1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v/1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
};

const MOCK_WATCHLIST = [
  { rank:1, ticker:"BCAX", sector:"BIO", composite:87.6, price_momentum:74.1, volume_surge:79.9, price_acceleration:94.9, rsi:98.2, stochastic:96.1, financial_health:94.7, news_attention:72.9, insider_activity:98.4, name:"Bicara Therapeutics Inc.", market_cap:891e6, price:14.32, change_7d:8.2, current_ratio:3.2, debt_to_equity:0.4, net_margin:-0.12, insider_details:[{who:"CEO",action:"bought",shares:5000,value:79400,date:"2026-02-15"},{who:"CFO",action:"bought",shares:2000,value:31200,date:"2026-01-22"}] },
  { rank:2, ticker:"UAMY", sector:"MINE", composite:86.8, price_momentum:97.8, volume_surge:97.6, price_acceleration:97.0, rsi:91.1, stochastic:97.8, financial_health:79.5, news_attention:93.2, insider_activity:50.0, name:"United States Antimony Corp", market_cap:723e6, price:28.45, change_7d:12.4, current_ratio:1.8, debt_to_equity:1.2, net_margin:0.03, insider_details:[] },
  { rank:3, ticker:"LYEL", sector:"BIO", composite:85.3, price_momentum:92.2, volume_surge:61.5, price_acceleration:77.0, rsi:74.7, stochastic:95.9, financial_health:83.5, news_attention:93.2, insider_activity:98.4, name:"Lyell Immunopharma Inc.", market_cap:567e6, price:4.87, change_7d:5.6, current_ratio:4.1, debt_to_equity:0.1, net_margin:-0.85, insider_details:[{who:"Director",action:"bought",shares:100000,value:24999970,date:"2026-02-28"}] },
  { rank:4, ticker:"RLMD", sector:"PHRM", composite:84.3, price_momentum:99.8, volume_surge:100.0, price_acceleration:100.0, rsi:95.9, stochastic:88.2, financial_health:58.5, news_attention:59.2, insider_activity:72.8, name:"Relmada Therapeutics Inc.", market_cap:612e6, price:22.10, change_7d:18.7, current_ratio:2.4, debt_to_equity:0.01, net_margin:-26.18, insider_details:[{who:"VP",action:"bought",shares:1500,value:33150,date:"2026-03-01"}] },
  { rank:5, ticker:"TALK", sector:"TECH", composite:83.3, price_momentum:74.8, volume_surge:99.7, price_acceleration:89.3, rsi:94.0, stochastic:98.9, financial_health:98.5, news_attention:80.0, insider_activity:50.0, name:"Talkspace Inc.", market_cap:534e6, price:5.12, change_7d:6.3, current_ratio:2.7, debt_to_equity:0.44, net_margin:0.05, insider_details:[] },
  { rank:6, ticker:"ISSC", sector:"AERO", composite:83.3, price_momentum:93.0, volume_surge:96.7, price_acceleration:95.1, rsi:95.7, stochastic:89.3, financial_health:95.6, news_attention:50.0, insider_activity:50.0, name:"Innovative Solutions & Support", market_cap:501e6, price:9.88, change_7d:4.1, current_ratio:2.5, debt_to_equity:1.1, net_margin:0.19, insider_details:[] },
  { rank:7, ticker:"KYTX", sector:"BIO", composite:82.9, price_momentum:94.9, volume_surge:65.4, price_acceleration:82.3, rsi:82.7, stochastic:76.0, financial_health:90.6, news_attention:59.2, insider_activity:93.2, name:"Kyverna Therapeutics Inc.", market_cap:789e6, price:18.33, change_7d:7.8, current_ratio:5.6, debt_to_equity:0.05, net_margin:-1.40, insider_details:[{who:"CEO",action:"bought",shares:8000,value:146560,date:"2026-02-10"}] },
  { rank:8, ticker:"SLS", sector:"BIO", composite:82.8, price_momentum:97.5, volume_surge:87.9, price_acceleration:91.4, rsi:96.1, stochastic:91.9, financial_health:94.7, news_attention:50.0, insider_activity:50.0, name:"SELLAS Life Sciences Group", market_cap:645e6, price:3.22, change_7d:11.0, current_ratio:3.8, debt_to_equity:0.2, net_margin:-0.45, insider_details:[] },
  { rank:9, ticker:"IPI", sector:"CHEM", composite:81.6, price_momentum:77.6, volume_surge:97.9, price_acceleration:89.5, rsi:93.2, stochastic:76.2, financial_health:88.2, news_attention:93.2, insider_activity:50.0, name:"Intrepid Potash Inc", market_cap:712e6, price:34.56, change_7d:9.2, current_ratio:1.3, debt_to_equity:0.56, net_margin:0.001, insider_details:[] },
  { rank:10, ticker:"LRMR", sector:"BIO", composite:79.7, price_momentum:42.8, volume_surge:78.1, price_acceleration:99.5, rsi:92.8, stochastic:88.0, financial_health:94.7, news_attention:69.7, insider_activity:94.6, name:"Larimar Therapeutics Inc.", market_cap:556e6, price:7.91, change_7d:22.3, current_ratio:6.2, debt_to_equity:0.0, net_margin:-2.10, insider_details:[{who:"CEO",action:"bought",shares:50000,value:395500,date:"2026-03-05"},{who:"Director",action:"bought",shares:25000,value:197750,date:"2026-03-05"},{who:"COO",action:"bought",shares:30000,value:237300,date:"2026-02-20"}] },
  { rank:11, ticker:"PURR", sector:"FIN", composite:79.5, price_momentum:null, volume_surge:96.4, price_acceleration:null, rsi:85.4, stochastic:95.4, financial_health:88.2, news_attention:72.9, insider_activity:50.0, name:"Hyperliquid Strategies Inc", market_cap:823e6, price:11.20, change_7d:3.5, current_ratio:2.1, debt_to_equity:0.8, net_margin:0.15, insider_details:[] },
  { rank:12, ticker:"SWBI", sector:"DEFN", composite:79.4, price_momentum:79.2, volume_surge:95.3, price_acceleration:92.3, rsi:99.0, stochastic:99.6, financial_health:84.2, news_attention:50.0, insider_activity:50.0, name:"Smith & Wesson Brands Inc.", market_cap:678e6, price:15.67, change_7d:5.8, current_ratio:1.9, debt_to_equity:1.5, net_margin:0.08, insider_details:[] },
  { rank:13, ticker:"UMAC", sector:"TECH", composite:79.1, price_momentum:67.6, volume_surge:98.8, price_acceleration:98.1, rsi:94.4, stochastic:86.0, financial_health:83.5, news_attention:75.3, insider_activity:50.0, name:"Unusual Machines Inc.", market_cap:502e6, price:8.34, change_7d:15.2, current_ratio:2.3, debt_to_equity:0.3, net_margin:-0.22, insider_details:[] },
  { rank:14, ticker:"SGP", sector:"PHRM", composite:79.0, price_momentum:null, volume_surge:null, price_acceleration:null, rsi:null, stochastic:null, financial_health:null, news_attention:50.0, insider_activity:98.4, name:"SpyGlass Pharma Inc.", market_cap:934e6, price:6.50, change_7d:-1.2, current_ratio:null, debt_to_equity:null, net_margin:null, insider_details:[{who:"CEO",action:"bought",shares:200000,value:119040000,date:"2026-02-18"}] },
  { rank:15, ticker:"IMMX", sector:"BIO", composite:78.4, price_momentum:98.3, volume_surge:71.9, price_acceleration:95.3, rsi:99.4, stochastic:96.7, financial_health:70.0, news_attention:50.0, insider_activity:50.0, name:"Immix Biopharma Inc.", market_cap:518e6, price:12.78, change_7d:9.9, current_ratio:1.5, debt_to_equity:2.1, net_margin:-0.67, insider_details:[] },
  { rank:16, ticker:"SLDB", sector:"BIO", composite:77.9, price_momentum:89.2, volume_surge:96.2, price_acceleration:98.6, rsi:94.9, stochastic:99.8, financial_health:75.2, news_attention:50.0, insider_activity:32.5, name:"Solid Biosciences Inc.", market_cap:601e6, price:9.45, change_7d:13.4, current_ratio:2.8, debt_to_equity:0.7, net_margin:-1.30, insider_details:[{who:"CFO",action:"sold",shares:10000,value:94500,date:"2026-02-25"}] },
  { rank:17, ticker:"NBR", sector:"ENGY", composite:77.8, price_momentum:88.5, volume_surge:82.8, price_acceleration:57.7, rsi:92.6, stochastic:81.7, financial_health:81.0, news_attention:50.0, insider_activity:81.0, name:"Nabors Industries Ltd.", market_cap:845e6, price:72.30, change_7d:4.5, current_ratio:1.1, debt_to_equity:3.2, net_margin:0.02, insider_details:[{who:"Director",action:"bought",shares:500,value:36150,date:"2026-01-15"}] },
  { rank:18, ticker:"TEN", sector:"SHIP", composite:77.4, price_momentum:77.3, volume_surge:95.0, price_acceleration:92.6, rsi:98.8, stochastic:93.2, financial_health:null, news_attention:50.0, insider_activity:50.0, name:"Tsakos Energy Navigation", market_cap:756e6, price:25.80, change_7d:6.7, current_ratio:null, debt_to_equity:null, net_margin:null, insider_details:[] },
  { rank:19, ticker:"ANNX", sector:"BIO", composite:76.0, price_momentum:95.3, volume_surge:77.5, price_acceleration:74.9, rsi:84.8, stochastic:92.6, financial_health:94.7, news_attention:50.0, insider_activity:35.3, name:"Annexon Inc.", market_cap:623e6, price:16.42, change_7d:2.1, current_ratio:4.5, debt_to_equity:0.1, net_margin:-1.80, insider_details:[{who:"VP",action:"sold",shares:5000,value:82100,date:"2026-03-02"}] },
  { rank:20, ticker:"ATEX", sector:"TELC", composite:75.6, price_momentum:80.1, volume_surge:76.3, price_acceleration:87.4, rsi:96.9, stochastic:76.6, financial_health:86.4, news_attention:50.0, insider_activity:54.3, name:"Anterix Inc.", market_cap:589e6, price:33.10, change_7d:3.8, current_ratio:3.0, debt_to_equity:0.6, net_margin:0.22, insider_details:[{who:"CEO",action:"bought",shares:1000,value:33100,date:"2026-01-30"}] },
];

const MOCK_PRICE_DATA = (() => {
  const tickers = MOCK_WATCHLIST.slice(0, 10).map(s => s.ticker);
  const data = {};
  tickers.forEach(ticker => {
    const points = [];
    let price = 5 + Math.random() * 20;
    for (let i = 250; i >= 0; i--) {
      const date = new Date(); date.setDate(date.getDate() - i);
      price = price * (1 + (Math.random() - 0.45) * 0.04);
      points.push({ date: date.toISOString().split("T")[0], close: price });
    }
    data[ticker] = points;
  });
  return data;
})();

// ═══════════════════════════════════════════════════════════
// COMPONENTS
// ═══════════════════════════════════════════════════════════

function HeroBanner() {
  return (
    <div style={{ position:"relative", overflow:"hidden", height:320, background:"linear-gradient(180deg, #0a0a0a 0%, #1a0a1a 50%, #0a0a0a 100%)", display:"flex", alignItems:"center", justifyContent:"center", borderBottom:"2px solid #ff6a0055" }}>
      <div style={{ position:"absolute", bottom:0, left:0, right:0, height:"60%", perspective:400, overflow:"hidden" }}>
        <div style={{ position:"absolute", bottom:-20, left:"-20%", right:"-20%", height:"200%", backgroundImage:`linear-gradient(90deg, rgba(255,106,0,0.15) 1px, transparent 1px), linear-gradient(0deg, rgba(255,106,0,0.15) 1px, transparent 1px)`, backgroundSize:"60px 60px", transform:"rotateX(60deg)", transformOrigin:"center bottom" }} />
        <div style={{ position:"absolute", top:0, left:0, right:0, height:4, background:"linear-gradient(90deg, transparent, #ff4500, #ff6a00, #ff4500, transparent)", boxShadow:"0 0 40px 10px rgba(255,106,0,0.4)" }} />
      </div>
      <svg style={{ position:"absolute", bottom:"38%", left:0, width:"100%", height:80, opacity:0.3 }} viewBox="0 0 1200 80" preserveAspectRatio="none">
        <polygon fill="#ff4500" points="0,80 100,30 200,50 350,10 500,45 600,20 750,40 850,5 950,35 1050,25 1200,80" />
      </svg>
      <div style={{ position:"relative", zIndex:2, textAlign:"center" }}>
        <h1 style={{ fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:48, color:"#fff", letterSpacing:6, textShadow:"0 0 20px rgba(255,106,0,0.8), 0 0 60px rgba(255,106,0,0.4), 3px 3px 0 #ff4500", margin:0, lineHeight:1.2 }}>UNICORN</h1>
        <h1 style={{ fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:48, color:"#ff6a00", letterSpacing:6, textShadow:"0 0 20px rgba(255,106,0,0.8), 0 0 60px rgba(255,106,0,0.4), 3px 3px 0 #cc3300", margin:0, lineHeight:1.2 }}>HUNT</h1>
        <p style={{ fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#ff6a0099", marginTop:16, letterSpacing:4 }}>SMALL CAP MOMENTUM SCREENER</p>
      </div>
    </div>
  );
}

function ControlPanel({ minCap, maxCap, setMinCap, setMaxCap, universeCount, onRefresh, onHardReset, isLoading }) {
  const [minErr, setMinErr] = useState("");
  const [maxErr, setMaxErr] = useState("");
  const validate = (min, max) => {
    let mErr = "", xErr = "";
    if (min && isNaN(Number(min))) mErr = "Numbers only";
    if (max && isNaN(Number(max))) xErr = "Numbers only";
    if (min && max && Number(max) <= Number(min)) xErr = "Must be > Min";
    setMinErr(mErr); setMaxErr(xErr);
  };
  const inputStyle = { background:"#1a1a2e", border:"1px solid #333355", borderRadius:"8px 0 0 8px", padding:"14px 16px", color:"#ff6a00", fontSize:22, fontFamily:"'Press Start 2P', monospace", boxSizing:"border-box", outline:"none", flex:1, minWidth:0 };
  const suffixStyle = { fontFamily:"'Press Start 2P', monospace", fontSize:18, color:"#555577", padding:"14px 12px 14px 8px", background:"#1a1a2e", border:"1px solid #333355", borderLeft:"none", borderRadius:"0 8px 8px 0", display:"flex", alignItems:"center" };
  const labelStyle = { fontFamily:"'IBM Plex Mono', monospace", fontSize:12, color:"#8888aa", marginBottom:4, display:"block" };

  return (
    <div style={{ maxWidth:480, margin:"0 auto", padding:"24px 20px", background:"#12121e", borderRadius:16, border:"1px solid #222244", marginTop:-40, position:"relative", zIndex:10 }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:16, color:"#ffffff", textAlign:"center", marginBottom:20, fontWeight:700 }}>Market Cap Range</h2>
      <div style={{ marginBottom:12 }}>
        <label style={labelStyle}>Minimum ($BN)</label>
        <div style={{ display:"flex" }}>
          <input style={inputStyle} value={minCap} placeholder="0.5" onChange={e => { setMinCap(e.target.value); validate(e.target.value, maxCap); }} />
          <span style={suffixStyle}>B</span>
        </div>
        {minErr && <span style={{ color:"#ff4444", fontSize:10, fontFamily:"monospace" }}>{minErr}</span>}
      </div>
      <div style={{ textAlign:"center", color:"#444466", fontSize:20, margin:"4px 0" }}>↕</div>
      <div style={{ marginBottom:16 }}>
        <label style={labelStyle}>Maximum ($BN)</label>
        <div style={{ display:"flex" }}>
          <input style={inputStyle} value={maxCap} placeholder="1.5" onChange={e => { setMaxCap(e.target.value); validate(minCap, e.target.value); }} />
          <span style={suffixStyle}>B</span>
        </div>
        {maxErr && <span style={{ color:"#ff4444", fontSize:10, fontFamily:"monospace" }}>{maxErr}</span>}
      </div>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"12px 0", borderTop:"1px solid #222244", marginBottom:16, fontFamily:"'IBM Plex Mono', monospace", fontSize:13, color:"#8888aa" }}>
        <span>Universe size:</span>
        <span style={{ color:"#ffffff", fontWeight:700 }}>{universeCount} stocks</span>
      </div>
      <div style={{ display:"flex", gap:10 }}>
        <button onClick={onRefresh} disabled={isLoading} style={{ flex:1, padding:"14px 0", borderRadius:8, border:"none", cursor:"pointer", background:"linear-gradient(135deg, #4444ff, #6666ff)", color:"#fff", fontFamily:"'IBM Plex Mono', monospace", fontSize:14, fontWeight:700, opacity:isLoading?0.5:1 }}>
          {isLoading ? "Running..." : "⟳ Refresh"}
        </button>
        <button onClick={onHardReset} disabled={isLoading} style={{ flex:1, padding:"14px 0", borderRadius:8, border:"1px solid #444466", cursor:"pointer", background:"transparent", color:"#ff6a00", fontFamily:"'IBM Plex Mono', monospace", fontSize:14, fontWeight:700, opacity:isLoading?0.5:1 }}>
          ⚡ Hard Reset
        </button>
      </div>
    </div>
  );
}

function SignalCard({ signal, weight, onWeightChange, status }) {
  const isStale = status?.stale ?? false;
  const age = status?.age ?? 0;
  const ageStr = age < 1 ? `${Math.round(age * 24)}h ago` : `${age.toFixed(1)}d ago`;
  return (
    <div style={{ background:"#12121e", borderRadius:12, border:"1px solid #222244", padding:"16px 20px", marginBottom:8 }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }}>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <div style={{ width:10, height:10, borderRadius:"50%", background:isStale?"#ffcc00":"#00cc66", boxShadow:isStale?"0 0 8px #ffcc0088":"0 0 8px #00cc6688" }} />
          <span style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#ffffff", fontWeight:700 }}>{signal.name}</span>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:12 }}>
          <span style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#666688" }}>{ageStr}</span>
          <span style={{ fontFamily:"'Press Start 2P', monospace", fontSize:12, color:"#ff6a00", minWidth:40, textAlign:"right" }}>{weight}%</span>
        </div>
      </div>
      <div style={{ position:"relative", height:24, marginBottom:8 }}>
        <div style={{ position:"absolute", top:10, left:0, right:0, height:4, background:"#222244", borderRadius:2 }}>
          <div style={{ height:"100%", width:`${weight}%`, borderRadius:2, background:"linear-gradient(90deg, #4444ff, #6666ff)" }} />
        </div>
        <input type="range" min={0} max={50} value={weight} onChange={e => onWeightChange(Number(e.target.value))} style={{ position:"absolute", top:0, left:0, width:"100%", height:24, opacity:0, cursor:"pointer" }} />
      </div>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#888888", margin:0, lineHeight:1.5 }}>{signal.desc}</p>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:10, color:"#555577", margin:"4px 0 0 0" }}>Source: {signal.source}</p>
    </div>
  );
}

function WeightsPanel({ weights, setWeights, statuses }) {
  const handleWeightChange = (key, newVal) => {
    const oldVal = weights[key];
    const diff = newVal - oldVal;
    const otherKeys = Object.keys(weights).filter(k => k !== key);
    const otherTotal = otherKeys.reduce((s, k) => s + weights[k], 0);
    if (otherTotal === 0) return;
    const newWeights = { ...weights, [key]: newVal };
    otherKeys.forEach(k => {
      const proportion = weights[k] / otherTotal;
      newWeights[k] = Math.max(0, Math.round(weights[k] - diff * proportion));
    });
    const sum = Object.values(newWeights).reduce((s, v) => s + v, 0);
    if (sum !== 100) {
      const largest = otherKeys.reduce((a, b) => newWeights[a] > newWeights[b] ? a : b);
      newWeights[largest] += 100 - sum;
    }
    setWeights(newWeights);
  };
  return (
    <div style={{ maxWidth:480, margin:"20px auto", padding:"0 20px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:12, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Signal Weights</h2>
      {SIGNALS.map(sig => (
        <SignalCard key={sig.key} signal={sig} weight={weights[sig.key]} onWeightChange={val => handleWeightChange(sig.key, val)} status={statuses[sig.dataFile]} />
      ))}
    </div>
  );
}

function DetailPanel({ row }) {
  const marginPct = row.net_margin != null ? `${(row.net_margin * 100).toFixed(1)}%` : "N/A";
  const crStr = row.current_ratio != null ? row.current_ratio.toFixed(1) : "N/A";
  const deStr = row.debt_to_equity != null ? row.debt_to_equity.toFixed(1) : "N/A";
  const insiders = row.insider_details || [];

  return (
    <pre style={{ margin:0, color:"#33ff33", paddingLeft:5 }}>
      <span style={{ color:"#888888" }}>{"╔══ "}</span>
      <span style={{ color:"#ffffff" }}>{row.ticker}</span>
      <span style={{ color:"#888888" }}>{" — "}{row.name}{" "}</span>
      <span style={{ color:"#888888" }}>{"═".repeat(Math.max(0, 56 - row.ticker.length - row.name.length))}</span>
      <span style={{ color:"#888888" }}>{"╗"}</span>{"\n"}

      <span style={{ color:"#888888" }}>{"║ "}</span>
      <span style={{ color:"#8888aa" }}>{"Market Cap: "}</span><span style={{ color:"#ff6a00" }}>{fmtCap(row.market_cap)}</span>
      <span style={{ color:"#888888" }}>{"  │  "}</span>
      <span style={{ color:"#8888aa" }}>{"Sector: "}</span><span style={{ color:"#ffffff" }}>{row.sector}</span>
      <span style={{ color:"#888888" }}>{"  │  "}</span>
      <span style={{ color:"#8888aa" }}>{"Price: "}</span>
      <span style={{ color: row.change_7d >= 0 ? "#00cc66" : "#ff4444" }}>${row.price?.toFixed(2)} ({row.change_7d >= 0 ? "+" : ""}{row.change_7d}% 7d)</span>
      {"\n"}

      <span style={{ color:"#888888" }}>{"║──────────────────────────────────────────────────────────────"}</span>{"\n"}

      <span style={{ color:"#888888" }}>{"║ "}</span>
      <span style={{ color:"#8888aa" }}>{"Fundamentals:"}</span>{"\n"}
      <span style={{ color:"#888888" }}>{"║   "}</span>
      <span style={{ color:"#8888aa" }}>{"Current Ratio: "}</span><span style={{ color:"#33ff33" }}>{crStr}</span>
      <span style={{ color:"#888888" }}>{"  │  "}</span>
      <span style={{ color:"#8888aa" }}>{"D/E: "}</span><span style={{ color:"#33ff33" }}>{deStr}</span>
      <span style={{ color:"#888888" }}>{"  │  "}</span>
      <span style={{ color:"#8888aa" }}>{"Net Margin: "}</span><span style={{ color: row.net_margin != null && row.net_margin >= 0 ? "#00cc66" : "#ff6a00" }}>{marginPct}</span>
      {"\n"}

      <span style={{ color:"#888888" }}>{"║──────────────────────────────────────────────────────────────"}</span>{"\n"}

      <span style={{ color:"#888888" }}>{"║ "}</span>
      <span style={{ color:"#8888aa" }}>{"Insider Activity (90d):"}</span>
      {insiders.length === 0 && <span style={{ color:"#555577" }}>{" No Form 4 transactions"}</span>}
      {"\n"}
      {insiders.map((ins, i) => (
        <span key={i}>
          <span style={{ color:"#888888" }}>{"║   "}</span>
          <span style={{ color: ins.action === "bought" ? "#00cc66" : "#ff4444" }}>{"● "}</span>
          <span style={{ color:"#ffffff" }}>{ins.who}</span>
          <span style={{ color: ins.action === "bought" ? "#00cc66" : "#ff4444" }}>{` ${ins.action} `}</span>
          <span style={{ color:"#33ff33" }}>{ins.shares.toLocaleString()} shares</span>
          <span style={{ color:"#888888" }}>{" ("}</span>
          <span style={{ color:"#ff6a00" }}>{fmtVal(ins.value)}</span>
          <span style={{ color:"#888888" }}>{") — "}</span>
          <span style={{ color:"#8888aa" }}>{ins.date}</span>
          {"\n"}
        </span>
      ))}
      <span style={{ color:"#888888" }}>{"╚══════════════════════════════════════════════════════════════╝"}</span>{"\n"}
    </pre>
  );
}

function DOSTerminal({ watchlist }) {
  const [expandedRow, setExpandedRow] = useState(null);

  const pad = (str, len) => { str = String(str); return str.length >= len ? str.substring(0, len) : str + " ".repeat(len - str.length); };
  const padNum = (val, len) => { if (val === null || val === undefined) return pad("N/A", len); return pad(val.toFixed(1), len); };

  const exportCSV = () => {
    const headers = "Rank,Ticker,Sector,Composite,Momentum,Volume,Accel,RSI,Stoch,Health,News,Insider,Name,Market Cap,Price,Change 7d";
    const rows = watchlist.map(r => `${r.rank},${r.ticker},${r.sector},${r.composite},${r.price_momentum ?? ""},${r.volume_surge ?? ""},${r.price_acceleration ?? ""},${r.rsi ?? ""},${r.stochastic ?? ""},${r.financial_health ?? ""},${r.news_attention ?? ""},${r.insider_activity ?? ""},${r.name},${r.market_cap},${r.price},${r.change_7d}`);
    const csv = [headers, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob); const a = document.createElement("a");
    a.href = url; a.download = "unicorn_hunt_watchlist.csv"; a.click();
  };

  const headerLine = "RANK TCKR SEC  COMP  MOMNT VOLUM ACCEL  RSI  STOCH HLTH  NEWS  INSDR NAME";
  const divider = "═".repeat(headerLine.length);

  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:4, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Watchlist Output</h2>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", textAlign:"center", marginBottom:12 }}>Click any row to expand details</p>

      <div style={{ background:"#0a0a0a", border:"2px solid #333333", borderRadius:4, padding:0, overflow:"hidden" }}>
        <div style={{ background:"#0000aa", padding:"4px 12px", fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#ffffff", textAlign:"center" }}>
          UNICORN HUNT v1.0 — Top 20
        </div>
        <div style={{ padding:"12px 16px", overflowX:"auto", fontFamily:"'IBM Plex Mono', 'Courier New', monospace", fontSize:12, lineHeight:1.8 }}>
          <pre style={{ margin:0, color:"#33ff33" }}>
            <span style={{ color:"#ffcc00" }}>C:\UNICORN&gt;</span>{" run_signals.exe\n\n"}
            <span style={{ color:"#888888" }}>{divider}</span>{"\n"}
            <span style={{ color:"#ffffff" }}>{headerLine}</span>{"\n"}
            <span style={{ color:"#888888" }}>{divider}</span>{"\n"}
            {watchlist.map(row => (
              <span key={row.rank}>
                <span
                  onClick={() => setExpandedRow(expandedRow === row.rank ? null : row.rank)}
                  style={{ cursor:"pointer", display:"inline" }}
                >
                  <span style={{ color:"#33ff33" }}>{pad(row.rank, 5)}</span>
                  <span style={{ color:"#ffffff" }}>{pad(row.ticker, 5)}</span>
                  <span style={{ color:"#8888aa" }}>{pad(row.sector || "—", 5)}</span>
                  <span style={{ color:"#ff6a00", fontWeight:"bold" }}>{padNum(row.composite, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.price_momentum, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.volume_surge, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.price_acceleration, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.rsi, 5)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.stochastic, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.financial_health, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.news_attention, 6)}</span>
                  <span style={{ color:"#33ff33" }}>{padNum(row.insider_activity, 6)}</span>
                  <span style={{ color:"#888888" }}>{pad(row.name, 22)}</span>
                </span>
                {"\n"}
                {expandedRow === row.rank && <DetailPanel row={row} />}
              </span>
            ))}
            <span style={{ color:"#888888" }}>{divider}</span>{"\n"}
            <span style={{ color:"#ffcc00" }}>C:\UNICORN&gt;</span>
            <span style={{ animation:"blink 1s infinite" }}>_</span>
          </pre>
        </div>
      </div>

      <div style={{ display:"flex", gap:10, marginTop:12, justifyContent:"center" }}>
        <button onClick={exportCSV} style={{ padding:"10px 24px", borderRadius:6, border:"1px solid #333355", background:"transparent", color:"#33ff33", cursor:"pointer", fontFamily:"'IBM Plex Mono', monospace", fontSize:12 }}>↓ Export CSV</button>
        <button onClick={exportCSV} style={{ padding:"10px 24px", borderRadius:6, border:"1px solid #333355", background:"transparent", color:"#33ff33", cursor:"pointer", fontFamily:"'IBM Plex Mono', monospace", fontSize:12 }}>↓ Export Excel</button>
      </div>
    </div>
  );
}

function PriceChart({ ticker, data, rank }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || data.length === 0) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.offsetWidth * 2;
    const h = canvas.height = canvas.offsetHeight * 2;
    ctx.scale(2, 2);
    const cw = w / 2, ch = h / 2;
    const prices = data.map(d => d.close);
    const min = Math.min(...prices) * 0.95;
    const max = Math.max(...prices) * 1.05;
    const latest = prices[prices.length - 1];
    const first = prices[0];
    const change = ((latest - first) / first * 100).toFixed(1);
    const isUp = latest >= first;
    ctx.fillStyle = "#0a0a14"; ctx.fillRect(0, 0, cw, ch);
    ctx.strokeStyle = "#ffffff08"; ctx.lineWidth = 0.5;
    for (let i = 0; i < 5; i++) { const y = (ch - 40) * i / 4 + 20; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cw, y); ctx.stroke(); }
    ctx.beginPath(); ctx.strokeStyle = isUp ? "#00cc66" : "#ff4444"; ctx.lineWidth = 1.5;
    prices.forEach((p, i) => { const x = (i / (prices.length - 1)) * cw; const y = ch - 30 - ((p - min) / (max - min)) * (ch - 50); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
    ctx.stroke();
    const lastY = ch - 30 - ((prices[prices.length - 1] - min) / (max - min)) * (ch - 50);
    ctx.lineTo(cw, ch - 30); ctx.lineTo(0, ch - 30); ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, ch);
    grad.addColorStop(0, isUp ? "#00cc6633" : "#ff444433"); grad.addColorStop(1, "#00000000");
    ctx.fillStyle = grad; ctx.fill();
    ctx.fillStyle = "#ffffff"; ctx.font = "bold 13px 'IBM Plex Mono', monospace"; ctx.fillText(ticker, 8, 16);
    ctx.fillStyle = isUp ? "#00cc66" : "#ff4444"; ctx.font = "11px 'IBM Plex Mono', monospace";
    ctx.fillText(`$${latest.toFixed(2)}  ${isUp ? "↑" : "↓"}${change}%`, 8, 32);
    ctx.fillStyle = "#ff6a00"; ctx.font = "bold 10px 'Press Start 2P', monospace"; ctx.textAlign = "right";
    ctx.fillText(`#${rank}`, cw - 8, 16); ctx.textAlign = "left";
  }, [ticker, data, rank]);
  return <canvas ref={canvasRef} style={{ width:"100%", height:180, borderRadius:8, border:"1px solid #222244", background:"#0a0a14" }} />;
}

function ChartsGrid({ watchlist, priceData }) {
  const top10 = watchlist.slice(0, 10);
  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px 60px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:16, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Top 10 Price Charts</h2>
      <div style={{ display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:12 }}>
        {top10.slice(0, 9).map(stock => (
          <PriceChart key={stock.ticker} ticker={stock.ticker} data={priceData[stock.ticker]} rank={stock.rank} />
        ))}
      </div>
      {top10.length >= 10 && (
        <div style={{ marginTop:12, maxWidth:"33.33%", marginLeft:"auto", marginRight:"auto" }}>
          <PriceChart ticker={top10[9].ticker} data={priceData[top10[9].ticker]} rank={top10[9].rank} />
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════

export default function UnicornHunt() {
  const [minCap, setMinCap] = useState("0.5");
  const [maxCap, setMaxCap] = useState("1.5");
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [isLoading, setIsLoading] = useState(false);
  const [statuses, setStatuses] = useState(MOCK_STATUS);
  const [watchlist, setWatchlist] = useState(MOCK_WATCHLIST);

  const handleRefresh = () => { setIsLoading(true); setTimeout(() => setIsLoading(false), 2000); };
  const handleHardReset = () => { setIsLoading(true); setTimeout(() => setIsLoading(false), 3000); };

  return (
    <div style={{ minHeight:"100vh", background:"linear-gradient(180deg, #08080f 0%, #0c0c18 50%, #08080f 100%)", color:"#ffffff" }}>
      <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=IBM+Plex+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
      <style>{`
        @keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
        * { box-sizing: border-box; }
        input[type="range"]::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%; background: #6666ff; cursor: pointer; }
        ::selection { background: #ff6a0044; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0a0a14; }
        ::-webkit-scrollbar-thumb { background: #333355; border-radius: 3px; }
      `}</style>
      <HeroBanner />
      <ControlPanel minCap={minCap} maxCap={maxCap} setMinCap={setMinCap} setMaxCap={setMaxCap} universeCount={755} onRefresh={handleRefresh} onHardReset={handleHardReset} isLoading={isLoading} />
      <WeightsPanel weights={weights} setWeights={setWeights} statuses={statuses} />
      <DOSTerminal watchlist={watchlist} />
      <ChartsGrid watchlist={watchlist} priceData={MOCK_PRICE_DATA} />
    </div>
  );
}
