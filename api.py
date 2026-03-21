"""
Unicorn Hunt API v1.3

Run with: uvicorn api:app --reload --port 8000
"""

import os
import sys
import time
import math
import json
import threading
import subprocess
import numpy as np
import pandas as pd
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict

# Always use the same Python that launched this process —
# ensures subprocess calls work in cron without venv activation issues.
PYTHON = sys.executable

app = FastAPI(title="Unicorn Hunt API", version="1.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://unicornpunk.org",
        "https://www.unicornpunk.org",
        "https://smallcap-momentum.pages.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILES = {
    "universe": "data/universe.parquet",
    "prices": "data/prices_combined.parquet",
    "fundamentals": "data/fundamentals.parquet",
    "news": "data/news_attention.parquet",
    "insider": "data/insider_activity.parquet",
    "watchlist": "data/watchlist.parquet",
}

STALENESS = {
    "universe": 7, "prices": 1, "fundamentals": 30, "news": 1, "insider": 14,
}

SIC_SECTORS = {
    "28": "PHRM", "29": "PETR", "13": "OIL", "10": "MINE", "12": "MINE",
    "14": "MINE", "20": "FOOD", "35": "MACH", "36": "ELEC", "37": "TRAN",
    "38": "INST", "48": "TELC", "49": "UTIL", "50": "WHSL", "51": "WHSL",
    "52": "RETL", "53": "RETL", "54": "RETL", "55": "RETL", "56": "RETL",
    "57": "RETL", "58": "RETL", "59": "RETL", "60": "BANK", "61": "FIN",
    "62": "FIN", "63": "INSR", "64": "INSR", "65": "REAL", "67": "FIN",
    "73": "TECH", "80": "HLTH", "87": "ENGR", "15": "CNST", "16": "CNST",
    "17": "CNST", "27": "PRNT", "30": "RUBR", "33": "METL", "34": "METL",
    "39": "MISC", "40": "RAIL", "42": "TRUK", "44": "SHIP", "45": "AIRL",
    "47": "TRVL", "70": "HOTL", "72": "SVCS", "75": "AUTO", "76": "SVCS",
    "78": "MDIA", "79": "ENTR", "82": "EDUC", "83": "SOCL", "86": "MEMB",
}

PROGRESS_FILE = "data/.refresh_progress.json"
CONFIG_FILE = "config.json"
refresh_in_progress = False

# Fallback config if config.json is missing
_FALLBACK_CONFIG = {
    "universe": {
        "min_market_cap": 500000000,
        "max_market_cap": 2000000000,
    },
    "signal_weights": {
        "price_momentum": 0.20,
        "volume_surge": 0.20,
        "price_acceleration": 0.10,
        "rsi": 0.00,
        "stochastic": 0.10,
        "financial_health": 0.15,
        "news_attention": 0.05,
        "insider_activity": 0.20,
    }
}


def load_config():
    """Load config.json, return fallback if missing."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _FALLBACK_CONFIG


def write_progress(step, total_steps, step_name, detail="", percent=0):
    try:
        os.makedirs("data", exist_ok=True)
        with open(PROGRESS_FILE, "w") as f:
            json.dump({
                "step": step,
                "total_steps": total_steps,
                "step_name": step_name,
                "detail": detail,
                "percent": percent,
                "timestamp": time.time(),
            }, f)
    except Exception:
        pass


def read_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"step": 0, "total_steps": 6, "step_name": "", "detail": "", "percent": 0}


def clear_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
    except Exception:
        pass


def get_file_age_days(filepath):
    if not os.path.exists(filepath):
        return None
    return (time.time() - os.path.getmtime(filepath)) / 86400


def is_stale(source_name):
    filepath = DATA_FILES.get(source_name)
    if not filepath:
        return True
    age = get_file_age_days(filepath)
    if age is None:
        return True
    return age > STALENESS.get(source_name, 999)


def get_sector(sic_code):
    if not sic_code:
        return "—"
    return SIC_SECTORS.get(str(sic_code)[:2], "OTHR")


def safe_round(val, decimals=1):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


def build_watchlist_response(watchlist_path=None):
    if watchlist_path is None:
        watchlist_path = DATA_FILES["watchlist"]
    if not os.path.exists(watchlist_path):
        return {"error": "No watchlist found.", "data": []}

    df = pd.read_parquet(watchlist_path)

    universe = {}
    if os.path.exists(DATA_FILES["universe"]):
        for _, row in pd.read_parquet(DATA_FILES["universe"]).iterrows():
            universe[row["ticker"]] = {
                "market_cap": float(row.get("market_cap", 0)),
                "sic_code": str(row.get("sic_code", "")),
            }

    fundamentals = {}
    if os.path.exists(DATA_FILES["fundamentals"]):
        for _, row in pd.read_parquet(DATA_FILES["fundamentals"]).iterrows():
            fundamentals[row["ticker"]] = {
                "current_ratio": row.get("current_ratio"),
                "debt_to_equity": row.get("debt_to_equity"),
                "net_margin": row.get("net_margin"),
            }

    insider_data = {}
    if os.path.exists(DATA_FILES["insider"]):
        for _, row in pd.read_parquet(DATA_FILES["insider"]).iterrows():
            insider_data[row["ticker"]] = {
                "insider_buys": int(row.get("insider_buys", 0)),
                "insider_sells": int(row.get("insider_sells", 0)),
                "buy_value": float(row.get("buy_value", 0)),
                "sell_value": float(row.get("sell_value", 0)),
                "net_buy_value": float(row.get("net_buy_value", 0)),
            }

    price_info = {}
    if os.path.exists(DATA_FILES["prices"]):
        watchlist_tickers = df["ticker"].unique().tolist()
        pdf = pd.read_parquet(DATA_FILES["prices"], filters=[("ticker", "in", watchlist_tickers)])
        pdf["date"] = pd.to_datetime(pdf["date"])
        cutoff = pdf["date"].max() - pd.Timedelta(days=30)
        pdf = pdf[pdf["date"] >= cutoff]
        for ticker in watchlist_tickers:
            tdf = pdf[pdf["ticker"] == ticker].sort_values("date")
            if len(tdf) >= 6:
                latest = float(tdf.iloc[-1]["close"])
                prev = float(tdf.iloc[-6]["close"])
                chg = round((latest - prev) / prev * 100, 1) if prev > 0 else 0
                price_info[ticker] = {"price": round(latest, 2), "change_7d": chg}

    results = []
    for _, row in df.iterrows():
        ticker = row["ticker"]
        uni = universe.get(ticker, {})
        fund = fundamentals.get(ticker, {})
        ins = insider_data.get(ticker, {})
        pi = price_info.get(ticker, {})
        results.append({
            "rank": int(row.get("rank", 0)),
            "ticker": ticker,
            "name": row.get("name", ""),
            "sector": get_sector(uni.get("sic_code")),
            "market_cap": uni.get("market_cap"),
            "composite": safe_round(row.get("composite_score")),
            "price_momentum": safe_round(row.get("price_momentum")),
            "volume_surge": safe_round(row.get("volume_surge")),
            "price_acceleration": safe_round(row.get("price_acceleration")),
            "rsi": safe_round(row.get("rsi")),
            "stochastic": safe_round(row.get("stochastic")),
            "financial_health": safe_round(row.get("financial_health")),
            "news_attention": safe_round(row.get("news_attention")),
            "insider_activity": safe_round(row.get("insider_activity")),
            "price": pi.get("price"),
            "change_7d": pi.get("change_7d"),
            "current_ratio": safe_round(fund.get("current_ratio")),
            "debt_to_equity": safe_round(fund.get("debt_to_equity")),
            "net_margin": safe_round(fund.get("net_margin"), 4),
            "insider_buys": ins.get("insider_buys", 0),
            "insider_sells": ins.get("insider_sells", 0),
            "buy_value": ins.get("buy_value", 0),
            "sell_value": ins.get("sell_value", 0),
            "net_buy_value": ins.get("net_buy_value", 0),
        })
    return {"data": results}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    """
    Serve config.json to the frontend.
    Returns universe bounds and default signal weights as percentages (0-100).
    """
    cfg = load_config()
    universe = cfg.get("universe", _FALLBACK_CONFIG["universe"])
    raw_weights = cfg.get("signal_weights", _FALLBACK_CONFIG["signal_weights"])

    # Strip notes key, convert decimals to percentages for frontend sliders
    weights_pct = {
        k: round(v * 100)
        for k, v in raw_weights.items()
        if k != "notes"
    }

    return {
        "universe": {
            "min_market_cap": universe["min_market_cap"],
            "max_market_cap": universe["max_market_cap"],
            "min_market_cap_bn": round(universe["min_market_cap"] / 1e9, 2),
            "max_market_cap_bn": round(universe["max_market_cap"] / 1e9, 2),
        },
        "signal_weights": weights_pct,
    }


@app.get("/api/status")
def get_status():
    statuses = {}
    for name, filepath in DATA_FILES.items():
        if name == "watchlist":
            continue
        age = get_file_age_days(filepath)
        max_age = STALENESS.get(name, 999)
        statuses[name] = {
            "age": round(age, 2) if age is not None else None,
            "stale": age is None or age > max_age,
            "max_age_days": max_age,
            "exists": os.path.exists(filepath),
        }
    universe_count = 0
    if os.path.exists(DATA_FILES["universe"]):
        df_uni = pd.read_parquet(DATA_FILES["universe"])
        cfg = load_config()
        min_cap = cfg["universe"]["min_market_cap"]
        max_cap = cfg["universe"]["max_market_cap"]
        universe_count = len(df_uni[
            (df_uni["market_cap"] >= min_cap) &
            (df_uni["market_cap"] <= max_cap)
        ])
    return {
        "statuses": statuses,
        "universe_count": universe_count,
        "refresh_in_progress": refresh_in_progress,
    }


@app.get("/api/watchlist")
def get_watchlist():
    return build_watchlist_response()


@app.get("/api/prices/{ticker}")
def get_prices(ticker: str, days: int = 365):
    if not os.path.exists(DATA_FILES["prices"]):
        return {"error": "No price data found.", "data": []}
    df = pd.read_parquet(DATA_FILES["prices"])
    df["date"] = pd.to_datetime(df["date"])
    tdf = df[df["ticker"] == ticker.upper()].sort_values("date").tail(days)
    if tdf.empty:
        return {"error": f"No data for {ticker}", "data": []}
    return {
        "ticker": ticker.upper(),
        "data": [
            {"date": row["date"].strftime("%Y-%m-%d"), "close": round(float(row["close"]), 2)}
            for _, row in tdf.iterrows()
        ]
    }


@app.get("/api/progress")
def get_progress():
    prog = read_progress()
    return {
        "in_progress": refresh_in_progress,
        "step": prog.get("step", 0),
        "total_steps": prog.get("total_steps", 6),
        "step_name": prog.get("step_name", ""),
        "detail": prog.get("detail", ""),
        "percent": prog.get("percent", 0),
    }


class RecalcRequest(BaseModel):
    weights: Dict[str, float]


@app.post("/api/recalc")
def recalc_watchlist(req: RecalcRequest):
    weights = req.weights
    total = sum(weights.values())
    if total <= 0:
        return {"error": "Weights must sum to > 0"}
    weights = {k: v / total for k, v in weights.items()}

    watchlist_path = DATA_FILES["watchlist"]
    if not os.path.exists(watchlist_path):
        return {"error": "No watchlist found.", "data": []}

    df = pd.read_parquet(watchlist_path)

    # Only score signals with non-zero weight
    signal_columns = [k for k in weights.keys() if k in df.columns and weights.get(k, 0) > 0]

    def weighted_score(row):
        total_s = 0
        total_w = 0
        for col in signal_columns:
            val = row.get(col)
            # Treat missing/NaN as neutral 50 — consistent with runner.py
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = 50.0
            total_s += val * weights.get(col, 0)
            total_w += weights.get(col, 0)
        return total_s / total_w if total_w > 0 else np.nan

    df["composite_score"] = df.apply(weighted_score, axis=1)
    df = df.dropna(subset=["composite_score"])
    df["rank"] = df["composite_score"].rank(ascending=False).astype(int)
    df = df.sort_values("rank")
    df.to_parquet(watchlist_path, index=False)

    return build_watchlist_response(watchlist_path)


@app.post("/api/refresh")
def trigger_refresh(background_tasks: BackgroundTasks):
    global refresh_in_progress
    if refresh_in_progress:
        return {"status": "already_running"}
    refresh_in_progress = True
    write_progress(0, 6, "Starting...", "", 0)
    background_tasks.add_task(run_refresh_with_progress, force=False)
    return {"status": "started"}


@app.post("/api/reset")
def trigger_reset(background_tasks: BackgroundTasks):
    global refresh_in_progress
    if refresh_in_progress:
        return {"status": "already_running"}
    refresh_in_progress = True
    write_progress(0, 6, "Starting...", "", 0)
    background_tasks.add_task(run_refresh_with_progress, force=True)
    return {"status": "started"}


def run_step(step_num, step_name, command, force=False, skip=False):
    base_pct = int((step_num - 1) / 6 * 100)
    step_pct = int(step_num / 6 * 100)

    if skip:
        write_progress(step_num, 6, step_name, "up to date, skipped", step_pct)
        time.sleep(0.3)
        return

    write_progress(step_num, 6, step_name, "starting...", base_pct)

    process = subprocess.Popen(
        command, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )

    for line in process.stdout:
        line = line.strip()
        if "Progress:" in line or "progress:" in line.lower():
            try:
                if "%" in line:
                    pct_str = line.split("(")[1].split("%")[0] if "(" in line else "0"
                    sub_pct = int(pct_str)
                    overall_pct = base_pct + int(sub_pct / 100 * (step_pct - base_pct))
                    detail = line.split("|")[0].strip() if "|" in line else line
                    write_progress(step_num, 6, step_name, detail, overall_pct)
            except (IndexError, ValueError):
                pass
        elif "Fetching tickers page" in line:
            write_progress(step_num, 6, step_name, line, base_pct + 2)
        elif "Completed in" in line:
            write_progress(step_num, 6, step_name, "done", step_pct)
        elif "Scored" in line:
            write_progress(step_num, 6, step_name, line.strip(), step_pct - 2)

    process.wait()
    write_progress(step_num, 6, step_name, "done", step_pct)


def run_refresh_with_progress(force=False):
    global refresh_in_progress
    try:
        # Step 1: Universe
        skip = not force and not is_stale("universe")
        run_step(1, "Universe", f"{PYTHON} src/data/universe.py --refresh", force=force, skip=skip)

        # Step 2: Prices
        if force or is_stale("prices"):
            cmd = f"{PYTHON} src/data/fetch_prices.py --refresh" if os.path.exists(DATA_FILES["prices"]) else f"{PYTHON} src/data/fetch_prices.py"
            run_step(2, "Prices", cmd, force=force)
        else:
            run_step(2, "Prices", "", skip=True)

        # Step 3: News (always refresh)
        run_step(3, "News", f"{PYTHON} src/data/fetch_news.py")

        # Step 4: Fundamentals
        skip = not force and not is_stale("fundamentals")
        run_step(4, "Fundamentals", f"{PYTHON} src/data/fetch_fundamentals.py --refresh", force=force, skip=skip)

        # Step 5: Insider
        skip = not force and not is_stale("insider")
        run_step(5, "Insider Activity", f"{PYTHON} src/data/fetch_insider.py --refresh", force=force, skip=skip)

        # Step 6: Signals
        run_step(6, "Running Signals", f"{PYTHON} -m src.signals.runner --save")

        write_progress(6, 6, "Complete", "All done!", 100)
        time.sleep(1)
    finally:
        refresh_in_progress = False
        clear_progress()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
