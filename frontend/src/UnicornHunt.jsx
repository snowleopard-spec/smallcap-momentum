import { useState, useEffect } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const SIGNALS = [
  { key: "price_momentum", name: "Price Momentum", desc: "Composite 3/6/12 month returns, skipping most recent month to avoid reversal. Captures sustained upward trend.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "volume_surge", name: "Volume Surge", desc: "Recent volume vs 60-day average, normalised by market cap. Measures conviction — is real money flowing in?", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "price_acceleration", name: "Price Acceleration", desc: "Rate of change of momentum. Catches stocks early in their move before they appear on simple screens.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "rsi", name: "RSI Momentum", desc: "14-day Relative Strength Index, tuned for momentum. Sweet spot 60-80 scores highest, overextended >85 gets penalised.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "stochastic", name: "Stochastic", desc: "Slow Stochastic (14,3,3) combining level (50%), crossover (30%), and trend (20%). Where price closed relative to its range.", source: "Polygon EOD Prices", dataFile: "prices" },
  { key: "financial_health", name: "Financial Health", desc: "Solvency, cash position, and filing recency from SEC 10-K/10-Q filings. Filters out distressed companies.", source: "SEC EDGAR XBRL", dataFile: "fundamentals" },
  { key: "news_attention", name: "News Attention", desc: "30-day article count and 7-day surge, direction-adjusted. For small caps, any media attention is meaningful.", source: "Polygon News API", dataFile: "news" },
  { key: "insider_activity", name: "Insider Buying", desc: "Form 4 insider purchases vs sales. Asymmetric: buying is strongly bullish, selling only mildly bearish.", source: "SEC EDGAR Form 4", dataFile: "insider" },
];

const RISK_SIGNALS = [
  { key: "sharpe", name: "Sharpe Ratio", desc: "Absolute risk-adjusted return. Mean return / volatility, annualised over rolling window." },
  { key: "ir_universe", name: "IR (Universe)", desc: "Information ratio vs equal-weighted universe average. Measures outperformance relative to your own stock pool." },
  { key: "ir_russell", name: "IR (Russell 2000)", desc: "Information ratio vs IWM (Russell 2000 ETF). Measures outperformance relative to the broader small-cap market." },
];

const fmtCap = v => { if(!v) return "N/A"; if(v>=1e9) return "$"+(v/1e9).toFixed(2)+"B"; return "$"+(v/1e6).toFixed(0)+"M"; };
const fmtVal = v => { if(!v&&v!==0) return "N/A"; if(v>=1e6) return "$"+(v/1e6).toFixed(1)+"M"; if(v>=1e3) return "$"+(v/1e3).toFixed(0)+"K"; return "$"+v.toFixed(0); };
const pad=(s,l)=>{s=String(s);return s.length>=l?s.substring(0,l):s+" ".repeat(l-s.length);};
const pn=(v,l)=>{if(v===null||v===undefined)return pad("N/A",l);return pad(v.toFixed(1),l);};
const pn2=(v,l)=>{if(v===null||v===undefined)return pad("N/A",l);return pad(v>=0?"+"+v.toFixed(2):v.toFixed(2),l);};

// ── Shared weight rebalance logic ────────────────────────────────────────────
function rebalanceWeights(weights, key, newValue) {
  const oldValue = weights[key];
  const diff = newValue - oldValue;
  const otherKeys = Object.keys(weights).filter(k => k !== key);
  const otherTotal = otherKeys.reduce((s, k) => s + weights[k], 0);
  if (!otherTotal) return weights;
  const nw = { ...weights, [key]: newValue };
  otherKeys.forEach(k => {
    nw[k] = Math.max(0, Math.round(weights[k] - diff * (weights[k] / otherTotal)));
  });
  const sum = Object.values(nw).reduce((s, v) => s + v, 0);
  if (sum !== 100) {
    const largest = otherKeys.reduce((a, b) => nw[a] > nw[b] ? a : b);
    nw[largest] += 100 - sum;
  }
  return nw;
}

// ── Pharma Toggle ────────────────────────────────────────────────────────────
function PharmaToggle({ excludePharma, setExcludePharma, pharmaCount, accentColor }) {
  const isOrange = accentColor === "orange";
  const activeColor = isOrange ? "#ff6a00" : "#00e5ff";
  const activeShadow = isOrange ? "rgba(255,106,0,0.5)" : "rgba(0,229,255,0.5)";
  const borderColor = isOrange ? "#333333" : "#1a4444";
  return (
    <div style={{ display:"flex", alignItems:"center", justifyContent:"center", gap:10, marginBottom:12 }}>
      <button
        onClick={() => setExcludePharma(!excludePharma)}
        style={{
          display:"flex", alignItems:"center", gap:8,
          padding:"6px 14px", borderRadius:4,
          border:`1px solid ${excludePharma ? activeColor : borderColor}`,
          background: excludePharma ? (isOrange ? "rgba(255,106,0,0.1)" : "rgba(0,229,255,0.1)") : "transparent",
          cursor:"pointer",
          fontFamily:"'IBM Plex Mono', 'Courier New', monospace",
          fontSize:11, color: excludePharma ? activeColor : "#666688",
          transition:"all 0.2s ease",
          boxShadow: excludePharma ? `0 0 8px ${activeShadow}` : "none",
        }}
      >
        <span style={{
          display:"inline-block", width:14, height:14,
          border:`2px solid ${excludePharma ? activeColor : "#444466"}`,
          borderRadius:2, position:"relative",
          background: excludePharma ? (isOrange ? "rgba(255,106,0,0.2)" : "rgba(0,229,255,0.2)") : "transparent",
        }}>
          {excludePharma && (
            <span style={{
              position:"absolute", top:0, left:2,
              color: activeColor, fontSize:12, fontWeight:"bold", lineHeight:"12px",
            }}>✓</span>
          )}
        </span>
        EXCLUDE PHARMA
        {excludePharma && pharmaCount > 0 && (
          <span style={{ color:"#888888", fontSize:10 }}>
            (−{pharmaCount})
          </span>
        )}
      </button>
    </div>
  );
}

// ── Hero Banner (NEW — with header-banner.png) ───────────────────────────────
function HeroBanner() {
  return (
    <div style={{ position:"relative", overflow:"hidden", background:"#0d0d0d", borderBottom:"2px solid #e07020" }}>
      {/* Scanline overlay */}
      <div style={{ position:"absolute", inset:0, background:"repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.08) 2px, rgba(0,0,0,0.08) 4px)", pointerEvents:"none", zIndex:3 }} />
      {/* Banner image */}
      <div style={{ position:"relative", width:"100%" }}>
        <img
          src="/header-banner.png"
          alt="Unicorn Hunt"
          style={{ display:"block", width:"100%", imageRendering:"pixelated", filter:"brightness(1.0) contrast(1.05)" }}
        />
        {/* Title overlaid on the brown earth area */}
        <div style={{ position:"absolute", bottom:"18%", left:0, right:0, zIndex:2, textAlign:"center", padding:"0 16px" }}>
          <h1 style={{ fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:"clamp(28px, 7vw, 56px)", lineHeight:1.3, color:"#fff", textShadow:"0 0 14px rgba(224,112,32,0.8), 0 0 50px rgba(224,112,32,0.35), 3px 3px 0 rgba(0,0,0,0.95), -1px -1px 0 rgba(0,0,0,0.7)", letterSpacing:3, margin:0 }}>
            UNICORN{" "}
            <span style={{ color:"#b85010", textShadow:"0 0 10px rgba(160,60,10,0.6), 3px 3px 0 rgba(0,0,0,0.95), -1px -1px 0 rgba(0,0,0,0.7), 0 0 20px rgba(0,0,0,0.8)" }}>HUNT</span>
          </h1>
        </div>
      </div>
      {/* Spacer below image */}
      <div style={{ background:"#0d0d0d", height:4 }} />
      {/* Glow bar */}
      <div style={{ height:3, background:"linear-gradient(90deg, transparent, #e07020 20%, #e07020 80%, transparent)", boxShadow:"0 0 12px #e07020" }} />
    </div>
  );
}

// ── Progress Bar ─────────────────────────────────────────────────────────────
function ProgressBar({ progress, type }) {
  if(!progress) return null;
  const isRefresh = type==="refresh";
  const pct = progress.percent||0;
  return (
    <div style={{ marginTop:12 }}>
      <div style={{ display:"flex", justifyContent:"space-between", marginBottom:4, fontFamily:"'IBM Plex Mono', monospace", fontSize:11 }}>
        <span style={{ color:isRefresh?"#ff6a00":"#00cc66" }}>{progress.step_name||"Processing..."}{progress.step&&progress.total_steps?" ("+progress.step+"/"+progress.total_steps+")":""}</span>
        <span style={{ color:"#8888aa" }}>{pct}%</span>
      </div>
      <div style={{ height:8, background:"#222244", borderRadius:4, overflow:"hidden" }}>
        <div style={{ height:"100%", borderRadius:4, transition:"width 0.3s ease", width:pct+"%", background:isRefresh?"linear-gradient(90deg, #ff4500, #ff6a00)":"linear-gradient(90deg, #00aa55, #00cc66)", boxShadow:"0 0 10px "+(isRefresh?"rgba(255,106,0,0.5)":"rgba(0,204,102,0.5)") }} />
      </div>
      {progress.detail && <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:10, color:"#555577", marginTop:4, textAlign:"center" }}>{progress.detail}</p>}
    </div>
  );
}

// ── Control Panel (cap range + universe count only) ──────────────────────────
function ControlPanel({ minCapBn, maxCapBn, universeCount, isLoading, progress }) {
  const roStyle = { background:"#0e0e1a", border:"1px solid #222244", borderRadius:"8px 0 0 8px", padding:"14px 16px", color:"#666688", fontSize:22, fontFamily:"'Press Start 2P', monospace", boxSizing:"border-box", flex:1, minWidth:0, cursor:"not-allowed" };
  const sfxS = { fontFamily:"'Press Start 2P', monospace", fontSize:18, color:"#555577", padding:"14px 12px 14px 8px", background:"#0e0e1a", border:"1px solid #222244", borderLeft:"none", borderRadius:"0 8px 8px 0", display:"flex", alignItems:"center" };
  const lblS = { fontFamily:"'IBM Plex Mono', monospace", fontSize:12, color:"#8888aa", marginBottom:4, display:"block" };
  return (
    <div style={{ maxWidth:480, margin:"0 auto", padding:"24px 20px", background:"#12121e", borderRadius:16, border:"1px solid #222244", marginTop:-40, position:"relative", zIndex:10 }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:16, color:"#ffffff", textAlign:"center", marginBottom:4, fontWeight:700 }}>Market Cap Range</h2>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:10, color:"#555577", textAlign:"center", marginBottom:16 }}>Configured in config.json — contact admin to change</p>
      <div style={{ marginBottom:12 }}>
        <label style={lblS}>Minimum ($BN)</label>
        <div style={{ display:"flex" }}>
          <input style={roStyle} value={minCapBn} readOnly />
          <span style={sfxS}>B</span>
        </div>
      </div>
      <div style={{ textAlign:"center", color:"#444466", fontSize:20, margin:"4px 0" }}>↕</div>
      <div style={{ marginBottom:16 }}>
        <label style={lblS}>Maximum ($BN)</label>
        <div style={{ display:"flex" }}>
          <input style={roStyle} value={maxCapBn} readOnly />
          <span style={sfxS}>B</span>
        </div>
      </div>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"12px 0", borderTop:"1px solid #222244", fontFamily:"'IBM Plex Mono', monospace", fontSize:13, color:"#8888aa" }}>
        <span>Universe size:</span>
        <span style={{ color:"#ffffff", fontWeight:700 }}>{universeCount} stocks</span>
      </div>
      {isLoading && progress && <ProgressBar progress={progress} type="refresh" />}
    </div>
  );
}

// ── Signal Cards (with zero-weight greyed-out styling) ───────────────────────
function SignalCard({ signal, weight, onWeightChange, status }) {
  const isStale=status?.stale??false; const age=status?.age??0;
  const ageStr=age===null?"missing":age<1?Math.round(age*24)+"h ago":age.toFixed(1)+"d ago";
  const isInactive = weight === 0;
  return (
    <div style={{
      background: isInactive ? "#0c0c0c" : "#12121e",
      borderRadius:12,
      border: isInactive ? "1px solid #5a3018" : "1px solid #2e2218",
      boxShadow: isInactive ? "0 0 6px rgba(90,48,24,0.25), inset 0 0 15px rgba(90,48,24,0.06)" : "none",
      opacity: isInactive ? 0.7 : 1,
      padding:"16px 20px",
      marginBottom:8,
      transition:"all 0.3s ease",
    }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }}>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <div style={{ width:10, height:10, borderRadius:"50%", background: isInactive ? "#5a3018" : (isStale?"#ffcc00":"#ff6a00"), boxShadow: isInactive ? "0 0 4px #5a301844" : (isStale?"0 0 8px #ffcc0088":"0 0 8px #ff6a0088") }} />
          <span style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color: isInactive ? "#8a6a3a" : "#ffffff", fontWeight:700 }}>{signal.name}</span>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:12 }}>
          <span style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color: isInactive ? "#7a5a2a" : "#666688" }}>{ageStr}</span>
          <span style={{ fontFamily:"'Press Start 2P', monospace", fontSize:12, color: isInactive ? "#7a5a2a" : "#ff6a00", minWidth:40, textAlign:"right" }}>{weight}%</span>
        </div>
      </div>
      <div style={{ position:"relative", height:24, marginBottom:8 }}>
        <div style={{ position:"absolute", top:10, left:0, right:0, height:4, background:"#222244", borderRadius:2 }}>
          <div style={{ height:"100%", width:(weight*2)+"%", borderRadius:2, background: isInactive ? "#333" : "linear-gradient(90deg, #cc4400, #ff6a00)", boxShadow: isInactive ? "none" : undefined }} />
        </div>
        <input type="range" min={0} max={50} value={weight} onChange={e=>onWeightChange(Number(e.target.value))} style={{ position:"absolute", top:0, left:0, width:"100%", height:24, opacity:0, cursor:"pointer" }} />
      </div>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color: isInactive ? "#7a6a4a" : "#888888", margin:0, lineHeight:1.5 }}>{signal.desc}</p>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:10, color: isInactive ? "#6a5a3a" : "#555577", margin:"4px 0 0 0" }}>Source: {signal.source}</p>
    </div>
  );
}

function RiskSignalCard({ signal, weight, onWeightChange }) {
  return (
    <div style={{ background:"#12121e", borderRadius:12, border:"1px solid #1a2e2e", padding:"16px 20px", marginBottom:8 }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }}>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <div style={{ width:10, height:10, borderRadius:"50%", background:"#00e5ff", boxShadow:"0 0 8px #00e5ff88" }} />
          <span style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#ffffff", fontWeight:700 }}>{signal.name}</span>
        </div>
        <span style={{ fontFamily:"'Press Start 2P', monospace", fontSize:12, color:"#00e5ff", minWidth:40, textAlign:"right" }}>{weight}%</span>
      </div>
      <div style={{ position:"relative", height:24, marginBottom:8 }}>
        <div style={{ position:"absolute", top:10, left:0, right:0, height:4, background:"#222244", borderRadius:2 }}>
          <div style={{ height:"100%", width:weight+"%", borderRadius:2, background:"linear-gradient(90deg, #0088aa, #00e5ff)" }} />
        </div>
        <input type="range" min={0} max={100} value={weight} onChange={e=>onWeightChange(Number(e.target.value))} style={{ position:"absolute", top:0, left:0, width:"100%", height:24, opacity:0, cursor:"pointer" }} />
      </div>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#888888", margin:0, lineHeight:1.5 }}>{signal.desc}</p>
    </div>
  );
}

// ── Quant Signals Section ────────────────────────────────────────────────────
function QuantSignalsSection({ weights, setWeights, statuses, onRecalc, isRecalcing, recalcProgress }) {
  const handleWeightChange = (key, nv) => setWeights(rebalanceWeights(weights, key, nv));
  return (
    <div style={{ maxWidth:480, margin:"48px auto 20px", padding:"0 20px" }}>
      <div style={{ textAlign:"center", marginBottom:20 }}>
        <div style={{ width:60, height:2, background:"linear-gradient(90deg, transparent, #ff6a00, transparent)", margin:"0 auto 20px" }} />
        <h2 style={{ fontFamily:"'Press Start 2P', monospace", fontSize:14, color:"#ff6a00", textShadow:"0 0 20px rgba(255,106,0,0.4)", letterSpacing:3, marginBottom:6 }}>QUANT SIGNALS</h2>
        <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", letterSpacing:1 }}>8 weighted momentum & quality signals — composite scoring</p>
      </div>
      {SIGNALS.map(sig=><SignalCard key={sig.key} signal={sig} weight={weights[sig.key]||0} onWeightChange={v=>handleWeightChange(sig.key,v)} status={statuses[sig.dataFile]} />)}
      <button onClick={onRecalc} disabled={isRecalcing} style={{ width:"100%", marginTop:12, padding:"14px 0", borderRadius:12, border:"2px solid #ff6a00", cursor:"pointer", background:"transparent", color:"#ff6a00", fontFamily:"'Press Start 2P', monospace", fontSize:11, letterSpacing:1, opacity:isRecalcing?0.5:1 }}>
        {isRecalcing ? "Calculating..." : "⚡ Recalc Momentum Rankings"}
      </button>
      {isRecalcing && recalcProgress && <ProgressBar progress={recalcProgress} type="recalc" />}
    </div>
  );
}

// ── Detail Panel (expandable row) ────────────────────────────────────────────
function DetailPanel({ row }) {
  const mp=row.net_margin!=null?(row.net_margin*100).toFixed(1)+"%":"N/A", cr=row.current_ratio!=null?row.current_ratio.toFixed(1):"N/A", de=row.debt_to_equity!=null?row.debt_to_equity.toFixed(1):"N/A";
  const hb=row.buy_value>0, hs=row.sell_value>0;
  return (
    <pre style={{ margin:0, color:"#33ff33", paddingLeft:5 }}>
<span style={{ color:"#888888" }}>{"╔══ "}</span><span style={{ color:"#ffffff" }}>{row.ticker}</span><span style={{ color:"#888888" }}>{" — "}{row.name}{" "}{"═".repeat(Math.max(0,56-row.ticker.length-(row.name||"").length))}{"╗"}</span>{"\n"}
<span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#8888aa" }}>Market Cap: </span><span style={{ color:"#ff6a00" }}>{fmtCap(row.market_cap)}</span><span style={{ color:"#888888" }}> │ </span><span style={{ color:"#8888aa" }}>Sector: </span><span style={{ color:"#ffffff" }}>{row.sector||"—"}</span><span style={{ color:"#888888" }}> │ </span><span style={{ color:"#8888aa" }}>Price: </span><span style={{ color:row.change_7d>=0?"#00cc66":"#ff4444" }}>${row.price?.toFixed(2)} ({row.change_7d>=0?"+":""}{row.change_7d}% 7d)</span>{"\n"}
<span style={{ color:"#888888" }}>{"║──────────────────────────────────────────────────────────────"}</span>{"\n"}
<span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#8888aa" }}>Fundamentals:</span>{"\n"}
<span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#8888aa" }}>Current Ratio: </span><span style={{ color:"#33ff33" }}>{cr}</span><span style={{ color:"#888888" }}> │ </span><span style={{ color:"#8888aa" }}>D/E: </span><span style={{ color:"#33ff33" }}>{de}</span><span style={{ color:"#888888" }}> │ </span><span style={{ color:"#8888aa" }}>Net Margin: </span><span style={{ color:row.net_margin!=null&&row.net_margin>=0?"#00cc66":"#ff6a00" }}>{mp}</span>{"\n"}
<span style={{ color:"#888888" }}>{"║──────────────────────────────────────────────────────────────"}</span>{"\n"}
<span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#8888aa" }}>Insider Activity (90d):</span>{!hb&&!hs&&<span style={{ color:"#555577" }}> No Form 4 transactions</span>}{"\n"}
{hb&&<span><span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#00cc66" }}>● {row.insider_buys} buy{row.insider_buys!==1?"s":""}</span><span style={{ color:"#888888" }}> totalling </span><span style={{ color:"#ff6a00" }}>{fmtVal(row.buy_value)}</span>{"\n"}</span>}
{hs&&<span><span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#ff4444" }}>● {row.insider_sells} sale{row.insider_sells!==1?"s":""}</span><span style={{ color:"#888888" }}> totalling </span><span style={{ color:"#ff6a00" }}>{fmtVal(row.sell_value)}</span>{"\n"}</span>}
{(hb||hs)&&<span><span style={{ color:"#888888" }}>{"║ "}</span><span style={{ color:"#8888aa" }}>Net: </span><span style={{ color:row.net_buy_value>=0?"#00cc66":"#ff4444" }}>{row.net_buy_value>=0?"+":""}{fmtVal(Math.abs(row.net_buy_value))}{row.net_buy_value<0?" (net selling)":" (net buying)"}</span>{"\n"}</span>}
<span style={{ color:"#888888" }}>{"╚══════════════════════════════════════════════════════════════╝"}</span>{"\n"}
    </pre>
  );
}

// ── Momentum DOS Terminal ────────────────────────────────────────────────────
function DOSTerminal({ watchlist, excludePharma, setExcludePharma }) {
  const [xr, setXr] = useState(null);
  const filtered = excludePharma ? watchlist.filter(r => r.sector !== "PHRM") : watchlist;
  const pharmaCount = watchlist.slice(0, 20).filter(r => r.sector === "PHRM").length;
  const t20 = filtered.slice(0,20);
  const exportCSV=()=>{const src = excludePharma ? filtered : watchlist; const h="Rank,Ticker,Sector,Composite,Momentum,Volume,Accel,RSI,Stoch,Health,News,Insider,Name,Market Cap,Price,Change 7d";const rows=src.map(r=>r.rank+","+r.ticker+","+r.sector+","+r.composite+","+(r.price_momentum??"")+","+(r.volume_surge??"")+","+(r.price_acceleration??"")+","+(r.rsi??"")+","+(r.stochastic??"")+","+(r.financial_health??"")+","+(r.news_attention??"")+","+(r.insider_activity??"")+","+r.name+","+r.market_cap+","+r.price+","+r.change_7d);const blob=new Blob([[h,...rows].join("\n")],{type:"text/csv"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="unicorn_hunt_watchlist"+(excludePharma?"_ex_pharma":"")+".csv";a.click();};
  const hl="RANK TCKR SEC COMP MOMNT VOLUM ACCEL RSI STOCH HLTH NEWS INSDR NAME";
  const dv="═".repeat(hl.length);
  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:4, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Momentum Watchlist</h2>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", textAlign:"center", marginBottom:12 }}>Click any row to expand details</p>
      <PharmaToggle excludePharma={excludePharma} setExcludePharma={setExcludePharma} pharmaCount={pharmaCount} accentColor="orange" />
      <div style={{ background:"#0a0a0a", border:"2px solid #442200", borderRadius:4, overflow:"hidden" }}>
        <div style={{ background:"#663300", padding:"4px 12px", fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#ff6a00", textAlign:"center" }}>UNICORN HUNT v1.0 — Top 20{excludePharma ? " (ex. Pharma)" : ""}</div>
        <div style={{ padding:"12px 16px", overflowX:"auto", fontFamily:"'IBM Plex Mono', 'Courier New', monospace", fontSize:12, lineHeight:1.8 }}>
          <pre style={{ margin:0, color:"#33ff33" }}>
<span style={{ color:"#ffcc00" }}>C:\UNICORN&gt;</span>{" run_signals.exe"}{excludePharma ? " --exclude-pharma" : ""}{"\n\n"}<span style={{ color:"#888888" }}>{dv}</span>{"\n"}<span style={{ color:"#ffffff" }}>{hl}</span>{"\n"}<span style={{ color:"#888888" }}>{dv}</span>{"\n"}
{t20.map(row=>(
  <span key={row.rank}>
    <span onClick={()=>setXr(xr===row.rank?null:row.rank)} style={{ cursor:"pointer" }}>
      <span style={{ color:"#33ff33" }}>{pad(row.rank,5)}</span><span style={{ color:"#ffffff" }}>{pad(row.ticker,5)}</span><span style={{ color:"#8888aa" }}>{pad(row.sector||"—",5)}</span>
      <span style={{ color:"#ff6a00", fontWeight:"bold" }}>{pn(row.composite,6)}</span>
      <span style={{ color:"#33ff33" }}>{pn(row.price_momentum,6)}{pn(row.volume_surge,6)}{pn(row.price_acceleration,6)}{pn(row.rsi,5)}{pn(row.stochastic,6)}{pn(row.financial_health,6)}{pn(row.news_attention,6)}{pn(row.insider_activity,6)}</span>
      <span style={{ color:"#888888" }}>{pad(row.name||"",22)}</span>
    </span>{"\n"}
    {xr===row.rank&&<DetailPanel row={row}/>}
  </span>
))}
<span style={{ color:"#888888" }}>{dv}</span>{"\n"}<span style={{ color:"#ffcc00" }}>C:\UNICORN&gt;</span>{" "}<a href="/Signal_Engine_Mathematical_Specification.pdf" target="_blank" rel="noopener noreferrer" style={{ color:"#33ff33", textDecoration:"none" }} onMouseEnter={e=>e.target.style.color="#ffcc00"} onMouseLeave={e=>e.target.style.color="#33ff33"}>TECHNICAL_DOC.PDF</a>{"\n\n"}<span style={{ color:"#ffcc00" }}>C:\UNICORN&gt;</span><span style={{ animation:"blink 1s infinite" }}>_</span>
          </pre>
        </div>
      </div>
      <div style={{ display:"flex", gap:10, marginTop:12, justifyContent:"center" }}>
        <button onClick={exportCSV} style={{ padding:"10px 24px", borderRadius:6, border:"1px solid #333355", background:"transparent", color:"#33ff33", cursor:"pointer", fontFamily:"'IBM Plex Mono', monospace", fontSize:12 }}>↓ Export CSV{excludePharma ? " (ex. Pharma)" : ""}</button>
      </div>
    </div>
  );
}

// ── Risk Metrics Section ─────────────────────────────────────────────────────
function RiskMetricsSection({ weights, setWeights, lookbackDays, onRecalc, isRecalcing }) {
  const handleWeightChange = (key, nv) => setWeights(rebalanceWeights(weights, key, nv));
  return (
    <div style={{ maxWidth:480, margin:"48px auto 20px", padding:"0 20px" }}>
      <div style={{ textAlign:"center", marginBottom:20 }}>
        <div style={{ width:60, height:2, background:"linear-gradient(90deg, transparent, #00e5ff, transparent)", margin:"0 auto 20px" }} />
        <h2 style={{ fontFamily:"'Press Start 2P', monospace", fontSize:14, color:"#00e5ff", textShadow:"0 0 20px rgba(0,229,255,0.4)", letterSpacing:3, marginBottom:6 }}>RISK METRICS</h2>
        <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", letterSpacing:1 }}>Sharpe & Information Ratios — {lookbackDays} day lookback</p>
      </div>
      {RISK_SIGNALS.map(sig=><RiskSignalCard key={sig.key} signal={sig} weight={weights[sig.key]||0} onWeightChange={v=>handleWeightChange(sig.key,v)} />)}
      <button onClick={onRecalc} disabled={isRecalcing} style={{ width:"100%", marginTop:12, padding:"14px 0", borderRadius:12, border:"2px solid #00e5ff", cursor:"pointer", background:"transparent", color:"#00e5ff", fontFamily:"'Press Start 2P', monospace", fontSize:11, letterSpacing:1, opacity:isRecalcing?0.5:1 }}>
        {isRecalcing ? "Calculating..." : "⚡ Recalc Risk Rankings"}
      </button>
    </div>
  );
}

// ── Risk Metrics DOS Terminal ────────────────────────────────────────────────
function RiskMetricsTerminal({ data, momentumTickers, excludePharma, setExcludePharma }) {
  const [xr, setXr] = useState(null);
  const filtered = excludePharma ? data.filter(r => r.sector !== "PHRM") : data;
  const pharmaCount = data.slice(0, 20).filter(r => r.sector === "PHRM").length;
  const t20 = filtered.slice(0,20);
  const hl="RANK TCKR SEC COMP SHARPE IR-UNI IR-RUSS PRICE 7D MCAP NAME";
  const dv="═".repeat(hl.length);
  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px" }}>
      <h2 style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:14, color:"#8888aa", textAlign:"center", marginBottom:4, fontWeight:400, letterSpacing:2, textTransform:"uppercase" }}>Risk-Adjusted Rankings</h2>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", textAlign:"center", marginBottom:12 }}>Independent ranking of entire universe by blended Sharpe & IR</p>
      <PharmaToggle excludePharma={excludePharma} setExcludePharma={setExcludePharma} pharmaCount={pharmaCount} accentColor="cyan" />
      <div style={{ background:"#0a0a0a", border:"2px solid #1a4444", borderRadius:4, overflow:"hidden" }}>
        <div style={{ background:"#004444", padding:"4px 12px", fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#00ffff", textAlign:"center" }}>RISK METRICS v1.0 — Top 20 by Risk-Adjusted Return{excludePharma ? " (ex. Pharma)" : ""}</div>
        <div style={{ padding:"12px 16px", overflowX:"auto", fontFamily:"'IBM Plex Mono', 'Courier New', monospace", fontSize:12, lineHeight:1.8 }}>
          <pre style={{ margin:0, color:"#00e5ff" }}>
<span style={{ color:"#00ffcc" }}>C:\RISK&gt;</span>{" run_metrics.exe"}{excludePharma ? " --exclude-pharma" : ""}{"\n\n"}<span style={{ color:"#1a5555" }}>{dv}</span>{"\n"}<span style={{ color:"#ffffff" }}>{hl}</span>{"\n"}<span style={{ color:"#1a5555" }}>{dv}</span>{"\n"}
{t20.map(row=>{
  const chg = row.change_7d;
  const chgStr = chg!=null ? (chg>=0?"+"+chg.toFixed(1)+"%":chg.toFixed(1)+"%") : "N/A";
  const chgColor = chg!=null ? (chg>=0?"#00cc66":"#ff4444") : "#668888";
  const inMomentum = momentumTickers.has(row.ticker);
  return (
    <span key={row.rank}>
      <span onClick={()=>setXr(xr===row.rank?null:row.rank)} style={{ cursor:"pointer" }}>
        <span style={{ color:"#00e5ff" }}>{pad(row.rank,5)}</span>
        <span style={{ color:"#ffffff" }}>{pad(row.ticker,5)}</span>
        <span style={{ color:"#668888" }}>{pad(row.sector||"—",5)}</span>
        <span style={{ color:"#00ffcc", fontWeight:"bold" }}>{pn(row.composite,7)}</span>
        <span style={{ color:"#00e5ff" }}>{pn2(row.sharpe,8)}{pn2(row.ir_universe,7)}{pn2(row.ir_russell,8)}</span>
        <span style={{ color:"#aaaacc" }}>{pad(row.price!=null?"$"+row.price.toFixed(2):"N/A",9)}</span>
        <span style={{ color:chgColor }}>{pad(chgStr,6)}</span>
        <span style={{ color:"#668888" }}>{pad(fmtCap(row.market_cap),9)}</span>
        <span style={{ color:"#668888" }}>{pad(row.name||"",20)}</span>
        {inMomentum && <span style={{ color:"#ff6a00" }}> ★</span>}
      </span>{"\n"}
      {xr===row.rank && (
        <span>
          <span style={{ color:"#1a5555" }}>{" ├─ "}</span>
          <span style={{ color:"#668888" }}>Sharpe pctile: </span><span style={{ color:"#00e5ff" }}>{row.sharpe_pctile!=null?row.sharpe_pctile.toFixed(0)+"th":"N/A"}</span>
          <span style={{ color:"#1a5555" }}>{" │ "}</span>
          <span style={{ color:"#668888" }}>IR-Uni pctile: </span><span style={{ color:"#00e5ff" }}>{row.ir_universe_pctile!=null?row.ir_universe_pctile.toFixed(0)+"th":"N/A"}</span>
          <span style={{ color:"#1a5555" }}>{" │ "}</span>
          <span style={{ color:"#668888" }}>IR-Russ pctile: </span><span style={{ color:"#00e5ff" }}>{row.ir_russell_pctile!=null?row.ir_russell_pctile.toFixed(0)+"th":"N/A"}</span>
          {"\n"}
        </span>
      )}
    </span>
  );
})}
<span style={{ color:"#1a5555" }}>{dv}</span>{"\n"}
<span style={{ color:"#668888" }}>{" ★ = also in momentum top 20"}</span>{"\n\n"}
<span style={{ color:"#00ffcc" }}>C:\RISK&gt;</span><span style={{ animation:"blink 1s infinite", color:"#00e5ff" }}>_</span>
          </pre>
        </div>
      </div>
    </div>
  );
}

// ── 13D Activist Filings Section ─────────────────────────────────────────────
function ActivistFilingsSection() {
  return (
    <div style={{ maxWidth:480, margin:"48px auto 20px", padding:"0 20px" }}>
      <div style={{ textAlign:"center", marginBottom:20 }}>
        <div style={{ width:60, height:2, background:"linear-gradient(90deg, transparent, #ff44ff, transparent)", margin:"0 auto 20px" }} />
        <h2 style={{ fontFamily:"'Press Start 2P', monospace", fontSize:14, color:"#ff44ff", textShadow:"0 0 20px rgba(255,68,255,0.4)", letterSpacing:3, marginBottom:6 }}>ACTIVIST TRACKER</h2>
        <p style={{ fontFamily:"'IBM Plex Mono', monospace", fontSize:11, color:"#555577", letterSpacing:1 }}>SEC Schedule 13D filings — 90 day lookback</p>
      </div>
    </div>
  );
}

function ActivistFilingsTerminal({ data, momentumTickers, riskTickers }) {
  const [xr, setXr] = useState(null);
  const filings = data.slice(0, 20);
  const hl="DATE TCKR SEC FORM PRICE 7D MCAP NAME";
  const dv="═".repeat(hl.length);
  if (filings.length === 0) {
    return (
      <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px 60px" }}>
        <div style={{ background:"#0a0a0a", border:"2px solid #2a1a2e", borderRadius:4, overflow:"hidden" }}>
          <div style={{ background:"#330033", padding:"4px 12px", fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#ff44ff", textAlign:"center" }}>13D TRACKER v1.0 — No Filings in Last 90 Days</div>
          <div style={{ padding:"24px 16px", textAlign:"center", fontFamily:"'IBM Plex Mono', monospace", fontSize:12, color:"#555577" }}>
            No SC 13D filings found for universe tickers in the lookback period.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div style={{ maxWidth:960, margin:"24px auto", padding:"0 20px 60px" }}>
      <div style={{ background:"#0a0a0a", border:"2px solid #2a1a2e", borderRadius:4, overflow:"hidden" }}>
        <div style={{ background:"#330033", padding:"4px 12px", fontFamily:"'Press Start 2P', 'Courier New', monospace", fontSize:10, color:"#ff44ff", textAlign:"center" }}>13D TRACKER v1.0 — Recent Activist Filings ({filings.length})</div>
        <div style={{ padding:"12px 16px", overflowX:"auto", fontFamily:"'IBM Plex Mono', 'Courier New', monospace", fontSize:12, lineHeight:1.8 }}>
          <pre style={{ margin:0, color:"#dd66ff" }}>
<span style={{ color:"#3a1a3e" }}>{dv}</span>{"\n"}<span style={{ color:"#ffffff" }}>{hl}</span>{"\n"}<span style={{ color:"#3a1a3e" }}>{dv}</span>{"\n"}
{filings.map((row, idx)=>{
  const chg = row.change_7d;
  const chgStr = chg!=null ? (chg>=0?"+"+chg.toFixed(1)+"%":chg.toFixed(1)+"%") : "N/A";
  const chgColor = chg!=null ? (chg>=0?"#00cc66":"#ff4444") : "#775577";
  const inMomentum = momentumTickers.has(row.ticker);
  const inRisk = riskTickers.has(row.ticker);
  const formStr = row.form_type==="SC 13D/A" ? "13D/A (amnd)" : "13D (new) ";
  return (
    <span key={idx}>
      <span onClick={()=>setXr(xr===idx?null:idx)} style={{ cursor:"pointer" }}>
        <span style={{ color:"#bb88cc" }}>{pad(row.file_date||"N/A",11)}</span>
        <span style={{ color:"#ffffff" }}>{pad(row.ticker,5)}</span>
        <span style={{ color:"#775577" }}>{pad(row.sector||"—",5)}</span>
        <span style={{ color:row.form_type==="SC 13D"?"#ff44ff":"#bb88cc" }}>{pad(formStr,13)}</span>
        <span style={{ color:"#ccaadd" }}>{pad(row.price!=null?"$"+row.price.toFixed(2):"N/A",9)}</span>
        <span style={{ color:chgColor }}>{pad(chgStr,7)}</span>
        <span style={{ color:"#775577" }}>{pad(fmtCap(row.market_cap),10)}</span>
        <span style={{ color:"#dd99ee" }}>{pad((row.name||"").substring(0,22),22)}</span>
        {inMomentum && <span style={{ color:"#ff6a00" }}> ★</span>}
        {inRisk && <span style={{ color:"#00e5ff" }}> ◆</span>}
      </span>{"\n"}
      {xr===idx && (
        <span>
          <span style={{ color:"#3a1a3e" }}>{" ├─ "}</span>
          <span style={{ color:"#775577" }}>Company: </span><span style={{ color:"#dd99ee" }}>{row.name||"Unknown"}</span>{"\n"}
          {row.file_description && <span><span style={{ color:"#3a1a3e" }}>{" ├─ "}</span><span style={{ color:"#775577" }}>Description: </span><span style={{ color:"#ccaadd" }}>{row.file_description.substring(0,60)}</span>{"\n"}</span>}
        </span>
      )}
    </span>
  );
})}
<span style={{ color:"#3a1a3e" }}>{dv}</span>{"\n"}
<span style={{ color:"#775577" }}>{" ★ = in momentum top 20 ◆ = in risk-adjusted top 20"}</span>{"\n\n"}
<span style={{ color:"#cc44cc" }}>C:\ACTIVIST&gt;</span><span style={{ animation:"blink 1s infinite", color:"#ff44ff" }}>_</span>
          </pre>
        </div>
      </div>
    </div>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────────
export default function UnicornHunt() {
  // Config state
  const [minCapBn, setMinCapBn] = useState("0.5");
  const [maxCapBn, setMaxCapBn] = useState("2.5");
  const [configLoaded, setConfigLoaded] = useState(false);

  // Momentum state
  const [weights, setWeights] = useState({
    price_momentum:20, volume_surge:20, price_acceleration:10,
    rsi:0, stochastic:10, financial_health:15, news_attention:5, insider_activity:20
  });
  const [watchlist, setWatchlist] = useState([]);
  const [isRecalcing, setIsRecalcing] = useState(false);
  const [recalcProgress, setRecalcProgress] = useState(null);

  // Risk metrics state
  const [riskWeights, setRiskWeights] = useState({ sharpe:34, ir_universe:33, ir_russell:33 });
  const [riskMetrics, setRiskMetrics] = useState([]);
  const [riskLookback, setRiskLookback] = useState(63);
  const [isRiskRecalcing, setIsRiskRecalcing] = useState(false);

  // 13D filings state
  const [filings13d, setFilings13d] = useState([]);

  // Pharma filter — single toggle drives both terminals
  const [excludePharma, setExcludePharma] = useState(false);

  // Shared state
  const [statuses, setStatuses] = useState({});
  const [universeCount, setUniverseCount] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(null);

  // Overlap indicators (respect pharma filter)
  const filteredWatchlist = excludePharma ? watchlist.filter(r => r.sector !== "PHRM") : watchlist;
  const momentumTickers = new Set(filteredWatchlist.slice(0, 20).map(w => w.ticker));
  const riskTickers = new Set(riskMetrics.slice(0, 20).map(r => r.ticker));

  useEffect(() => {
    loadConfig();
    loadRiskConfig();
    loadStatus();
    loadWatchlist();
    loadRiskMetrics();
    load13dFilings();
  }, []);

  const loadConfig = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/config");
      const cfg = r.data;
      setMinCapBn(cfg.universe.min_market_cap_bn.toString());
      setMaxCapBn(cfg.universe.max_market_cap_bn.toString());
      setWeights(cfg.signal_weights);
      setConfigLoaded(true);
    } catch(e) {
      console.warn("Could not load config from API, using defaults");
      setConfigLoaded(true);
    }
  };

  const loadRiskConfig = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/risk-metrics-config");
      const cfg = r.data;
      setRiskWeights(cfg.weights);
      setRiskLookback(cfg.lookback_days);
    } catch(e) {
      console.warn("Could not load risk metrics config, using defaults");
    }
  };

  const loadStatus = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/status");
      setStatuses(r.data.statuses);
      setUniverseCount(r.data.universe_count);
    } catch(e) {}
  };

  const loadWatchlist = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/watchlist");
      setWatchlist(r.data.data || []);
    } catch(e) {}
  };

  const loadRiskMetrics = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/risk-metrics");
      setRiskMetrics(r.data.data || []);
      if (r.data.config) {
        setRiskLookback(r.data.config.lookback_days || 63);
      }
    } catch(e) {}
  };

  const load13dFilings = async () => {
    try {
      const r = await axios.get(API_BASE + "/api/13d-filings");
      setFilings13d(r.data.data || []);
    } catch(e) {}
  };

  // ── Momentum Recalc ─────────────────────────────────────────────────────────
  const handleRecalc = async () => {
    setIsRecalcing(true);
    setRecalcProgress({ step_name:"Re-weighting scores", detail:"Calculating composite scores...", percent:30 });
    try {
      const aw = {};
      for (const [k, v] of Object.entries(weights)) aw[k] = v / 100;
      setRecalcProgress({ step_name:"Re-weighting scores", detail:"Applying new weights to "+universeCount+" stocks...", percent:60 });
      const r = await axios.post(API_BASE + "/api/recalc", { weights: aw });
      setRecalcProgress({ step_name:"Updating rankings", detail:"Sorting by composite score...", percent:90 });
      await new Promise(res => setTimeout(res, 300));
      setWatchlist(r.data.data || []);
      setRecalcProgress({ step_name:"Complete", detail:"Rankings updated!", percent:100 });
      await new Promise(res => setTimeout(res, 500));
    } catch(e) {
      console.error("Recalc failed:", e);
    }
    setRecalcProgress(null);
    setIsRecalcing(false);
  };

  // ── Risk Metrics Recalc ─────────────────────────────────────────────────────
  const handleRiskRecalc = async () => {
    setIsRiskRecalcing(true);
    try {
      const aw = {};
      for (const [k, v] of Object.entries(riskWeights)) aw[k] = v / 100;
      const r = await axios.post(API_BASE + "/api/recalc-risk", { weights: aw });
      setRiskMetrics(r.data.data || []);
    } catch(e) {
      console.error("Risk recalc failed:", e);
    }
    setIsRiskRecalcing(false);
  };

  if (!configLoaded) return (
    <div style={{ minHeight:"100vh", background:"#08080f", display:"flex", alignItems:"center", justifyContent:"center" }}>
      <p style={{ fontFamily:"'IBM Plex Mono', monospace", color:"#555577" }}>Loading...</p>
    </div>
  );

  return (
    <div style={{ minHeight:"100vh", width:"100%", background:"linear-gradient(180deg, #08080f 0%, #0c0c18 50%, #08080f 100%)", color:"#ffffff" }}>
      <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=IBM+Plex+Mono:wght@400;500;700&display=swap" rel="stylesheet" />
      <style>{"@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}*{box-sizing:border-box}input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#6666ff;cursor:pointer}::selection{background:#ff6a0044}::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:#0a0a14}::-webkit-scrollbar-thumb{background:#333355;border-radius:3px}"}</style>

      <HeroBanner />

      <ControlPanel
        minCapBn={minCapBn}
        maxCapBn={maxCapBn}
        universeCount={universeCount}
        isLoading={isLoading}
        progress={progress}
      />

      {/* ── QUANT SIGNALS — orange theme ── */}
      <QuantSignalsSection
        weights={weights}
        setWeights={setWeights}
        statuses={statuses}
        onRecalc={handleRecalc}
        isRecalcing={isRecalcing}
        recalcProgress={recalcProgress}
      />

      <DOSTerminal watchlist={watchlist} excludePharma={excludePharma} setExcludePharma={setExcludePharma} />

      {/* ── Divider ── */}
      <div style={{ maxWidth:960, margin:"20px auto", padding:"0 20px" }}>
        <div style={{ height:1, background:"linear-gradient(90deg, transparent, #00e5ff33, #00e5ff, #00e5ff33, transparent)" }} />
      </div>

      {/* ── RISK METRICS — cyan theme ── */}
      <RiskMetricsSection
        weights={riskWeights}
        setWeights={setRiskWeights}
        lookbackDays={riskLookback}
        onRecalc={handleRiskRecalc}
        isRecalcing={isRiskRecalcing}
      />

      <RiskMetricsTerminal
        data={riskMetrics}
        momentumTickers={momentumTickers}
        excludePharma={excludePharma}
        setExcludePharma={setExcludePharma}
      />

      {/* ── Divider ── */}
      <div style={{ maxWidth:960, margin:"20px auto", padding:"0 20px" }}>
        <div style={{ height:1, background:"linear-gradient(90deg, transparent, #ff44ff33, #ff44ff, #ff44ff33, transparent)" }} />
      </div>

      {/* ── 13D ACTIVIST FILINGS — magenta theme ── */}
      <ActivistFilingsSection />

      <ActivistFilingsTerminal
        data={filings13d}
        momentumTickers={momentumTickers}
        riskTickers={riskTickers}
      />
    </div>
  );
}
