import { useState, useEffect, useRef } from "react";
import axios from "axios";

const API_BASE = "http://localhost:8000";

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
  const ageStr = age === null ? "missing" : age < 1 ? `${Math.round(age * 24)}h ago` : `${age.toFixed(1)}d ago`;
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
  const hasBuys = row.buy_value > 0;
  const hasSells = row.sell_value > 0;

  return (
    <pre style={{ margin:0, color:"#33ff33", paddingLeft:5 }}>
      <span style={{ color:"#888888" }}>{"╔══ "}</span>
      <span style={{ color:"#ffffff" }}>{row.ticker}</span>
      <span style={{ color:"#888888" }}>{" — "}{row.name}{" "}</span>
      <span style={{ color:"#888888" }}>{"═".repeat(Math.max(0, 56 - row.ticker.length - (row.name||"").length))}</span>
      <span style={{ color:"#888888" }}>{"╗"}</span>{"\n"}

      <span style={{ color:"#888888" }}>{"║ "}</span>
      <span style={{ color:"#8888aa" }}>{"Market Cap: "}</span><span style={{ color:"#ff6a00" }}>{fmtCap(row.market_cap)}</span>
      <span style={{ color:"#888888" }}>{"  │  "}</span>
      <span style={{ color:"#8888aa" }}>{"Sector: "}</span><span style={{ color:"#ffffff" }}>{row.sector || "—"}</span>
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
      {!hasBuys && !hasSells && <span style={{ color:"#555577" }}>{" No Form 4 transactions"}</span>}
      {"\n"}
      {hasBuys && (
        <span>
          <span style={{ color:"#888888" }}>{"║   "}</span>
          <span style={{ color:"#00cc66" }}>{"● "}</span>
          <span style={{ color:"#00cc66" }}>{row.insider_buys} buy{row.insider_buys !== 1 ? "s" : ""}</span>
          <span style={{ color:"#888888" }}>{" totalling "}</span>
          <span style={{ color:"#ff6a00" }}>{fmtVal(row.buy_value)}</span>
          {"\n"}
        </span>
      )}
      {hasSells && (
        <span>
          <span style={{ color:"#888888" }}>{"║   "}</span>
          <span style={{ color:"#ff4444" }}>{"● "}</span>
          <span style={{ color:"#ff4444" }}>{row.insider_sells} sale{row.insider_sells !== 1 ? "s" : ""}</span>
          <span style={{ color:"#888888" }}>{" totalling "}</span>
          <span style={{ color:"#ff6a00" }}>{fmtVal(row.sell_value)}</span>
          {"\n"}
        </span>
      )}
      {(hasBuys || hasSells) && (
        <span>
          <span style={{ color:"#888888" }}>{"║   "}</span>
          <span style={{ color:"#8888aa" }}>{"Net: "}</span>
          <span style={{ color: row.net_buy_value >= 0 ? "#00cc66" : "#ff4444" }}>{row.net_buy_value >= 0 ? "+" : ""}{fmtVal(Math.abs(row.net_buy_value))}{row.net_buy_value < 0 ? " (net selling)" : " (net buying)"}</span>
          {"\n"}
        </span>
      )}
      <span style={{ color:"#888888" }}>{"╚══════════════════════════════════════════════════════════════╝"}</span>{"\n"}
    </pre>
  );
}

function DOSTerminal({ watchlist }) {
  const [expandedRow, setExpandedRow] = useState(null);
  const top20 = watchlist.slice(0, 20);

  const pad = (str, len) => { str = String(str); return str.length >= len ? str.substring(0, len) : str + " ".repeat(len - str.length); };
  const padNum = (val, len) => { if (val === null || val === undefined) return pad("N/A", len); return pad(val.toFixed(1), len); };

  const exportCSV = () => {
    const headers = "Rank,Ticker,Sector,Composite,Momentum,Volume,Accel,RSI,Stoch,Health,News,Insider,Name,Market Cap,Price,Change 7d";
    const rows = watchlist.map(r => `${r.rank},${r.ticker},${r.sector},${r.composite},${r.price_momentum ?? ""},${r.volume_surge ?? ""},${r.price_acceleration ?? ""},${r.rsi ?? ""},${r.stochastic ?? ""},${r.financial_health ?? ""},${r.news_attention ?? ""},${r.insider_activity ?? ""},${r.name},${r.market_cap},${r.price},${r.change_7d}`);
    const blob = new Blob([[headers, ...rows].join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = "unicorn_hunt_watchlist.csv"; a.click();
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
            {top20.map(row => (
              <span key={row.rank}>
                <span onClick={() => setExpandedRow(expandedRow === row.rank ? null : row.rank)} style={{ cursor:"pointer" }}>
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
                  <span style={{ color:"#888888" }}>{pad(row.name || "", 22)}</span>
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
        <button onClick={() => exportCSV()} style={{ padding:"10px 24px", borderRadius:6, border:"1px solid #333355", background:"transparent", color:"#33ff33", cursor:"pointer", fontFamily:"'IBM Plex Mono', monospace", fontSize:12 }}>↓ Export Excel</button>
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

function ChartsGrid({ watchlist }) {
  const [priceData, setPriceData] = useState({});
  const top10 = watchlist.slice(0, 10);

  useEffect(() => {
    if (top10.length === 0) return;
    const fetchPrices = async () => {
      const data = {};
      for (const stock of top10) {
        try {
          const res = await axios.get(`${API_BASE}/api/prices/${stock.ticker}`);
          data[stock.ticker] = res.data.data;
        } catch (e) {
          console.error(`Failed to fetch prices for ${stock.ticker}`);
        }
      }
      setPriceData(data);
    };
    fetchPrices();
  }, [watchlist]);

  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px 60px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:16, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Top 10 Price Charts</h2>
      <div style={{ display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:12 }}>
        {top10.slice(0, 9).map(stock => (
          <PriceChart key={stock.ticker} ticker={stock.ticker} data={priceData[stock.ticker] || []} rank={stock.rank} />
        ))}
      </div>
      {top10.length >= 10 && (
        <div style={{ marginTop:12, maxWidth:"33.33%", marginLeft:"auto", marginRight:"auto" }}>
          <PriceChart ticker={top10[9].ticker} data={priceData[top10[9].ticker] || []} rank={top10[9].rank} />
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
  const [statuses, setStatuses] = useState({});
  const [watchlist, setWatchlist] = useState([]);
  const [universeCount, setUniverseCount] = useState(0);

  // Load status and watchlist on mount
  useEffect(() => {
    loadStatus();
    loadWatchlist();
  }, []);

  const loadStatus = async () => {
    try {
      const res = await axios.get(`${API_BASE}/api/status`);
      setStatuses(res.data.statuses);
      setUniverseCount(res.data.universe_count);
    } catch (e) {
      console.error("Failed to load status:", e);
    }
  };

  const loadWatchlist = async () => {
    try {
      const res = await axios.get(`${API_BASE}/api/watchlist`);
      setWatchlist(res.data.data || []);
    } catch (e) {
      console.error("Failed to load watchlist:", e);
    }
  };

  const handleRefresh = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/api/refresh`);
      // Poll for completion
      const poll = setInterval(async () => {
        const res = await axios.get(`${API_BASE}/api/status`);
        if (!res.data.refresh_in_progress) {
          clearInterval(poll);
          setIsLoading(false);
          setStatuses(res.data.statuses);
          setUniverseCount(res.data.universe_count);
          loadWatchlist();
        }
      }, 5000);
    } catch (e) {
      setIsLoading(false);
      console.error("Refresh failed:", e);
    }
  };

  const handleHardReset = async () => {
    setIsLoading(true);
    try {
      await axios.post(`${API_BASE}/api/reset`);
      const poll = setInterval(async () => {
        const res = await axios.get(`${API_BASE}/api/status`);
        if (!res.data.refresh_in_progress) {
          clearInterval(poll);
          setIsLoading(false);
          setStatuses(res.data.statuses);
          setUniverseCount(res.data.universe_count);
          loadWatchlist();
        }
      }, 5000);
    } catch (e) {
      setIsLoading(false);
      console.error("Reset failed:", e);
    }
  };

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
      <ControlPanel minCap={minCap} maxCap={maxCap} setMinCap={setMinCap} setMaxCap={setMaxCap} universeCount={universeCount} onRefresh={handleRefresh} onHardReset={handleHardReset} isLoading={isLoading} />
      <WeightsPanel weights={weights} setWeights={setWeights} statuses={statuses} />
      <DOSTerminal watchlist={watchlist} />
      <ChartsGrid watchlist={watchlist} />
    </div>
  );
}
