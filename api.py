"""
Unicorn Hunt API

Run with: uvicorn api:app --reload --port 8000
"""

import os
import time
import math
import subprocess
import pandas as pd
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Unicorn Hunt API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
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

refresh_in_progress = False


def get_file_age_days(filepath):
    if not os.path.exists(filepath):
        return None
    return (time.time() - os.path.getmtime(filepath)) / 86400


def get_sector(sic_code):
    if not sic_code:
        return "—"
    prefix = str(sic_code)[:2]
    return SIC_SECTORS.get(prefix, "OTHR")


def safe_round(val, decimals=1):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


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
        universe_count = len(pd.read_parquet(DATA_FILES["universe"]))

    return {
        "statuses": statuses,
        "universe_count": universe_count,
        "refresh_in_progress": refresh_in_progress,
    }


@app.get("/api/watchlist")
def get_watchlist():
    watchlist_path = DATA_FILES["watchlist"]
    if not os.path.exists(watchlist_path):
        return {"error": "No watchlist found. Run signals first.", "data": []}

    df = pd.read_parquet(watchlist_path)

    # Load universe for extra fields
    universe = {}
    if os.path.exists(DATA_FILES["universe"]):
        udf = pd.read_parquet(DATA_FILES["universe"])
        for _, row in udf.iterrows():
            universe[row["ticker"]] = {
                "market_cap": float(row.get("market_cap", 0)),
                "sic_code": str(row.get("sic_code", "")),
            }

    # Load fundamentals
    fundamentals = {}
    if os.path.exists(DATA_FILES["fundamentals"]):
        fdf = pd.read_parquet(DATA_FILES["fundamentals"])
        for _, row in fdf.iterrows():
            fundamentals[row["ticker"]] = {
                "current_ratio": row.get("current_ratio"),
                "debt_to_equity": row.get("debt_to_equity"),
                "net_margin": row.get("net_margin"),
            }

    # Load insider summary
    insider_data = {}
    if os.path.exists(DATA_FILES["insider"]):
        idf = pd.read_parquet(DATA_FILES["insider"])
        for _, row in idf.iterrows():
            insider_data[row["ticker"]] = {
                "insider_buys": int(row.get("insider_buys", 0)),
                "insider_sells": int(row.get("insider_sells", 0)),
                "buy_value": float(row.get("buy_value", 0)),
                "sell_value": float(row.get("sell_value", 0)),
                "net_buy_value": float(row.get("net_buy_value", 0)),
            }

    # Load latest prices
    price_info = {}
    if os.path.exists(DATA_FILES["prices"]):
        pdf = pd.read_parquet(DATA_FILES["prices"])
        pdf["date"] = pd.to_datetime(pdf["date"])
        for ticker in df["ticker"].unique():
            tdf = pdf[pdf["ticker"] == ticker].sort_values("date")
            if len(tdf) >= 6:
                latest = float(tdf.iloc[-1]["close"])
                prev = float(tdf.iloc[-6]["close"])
                chg = round((latest - prev) / prev * 100, 1) if prev > 0 else 0
                price_info[ticker] = {"price": round(latest, 2), "change_7d": chg}

    # Build response
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


@app.post("/api/refresh")
def trigger_refresh(background_tasks: BackgroundTasks):
    global refresh_in_progress
    if refresh_in_progress:
        return {"status": "already_running"}
    refresh_in_progress = True
    background_tasks.add_task(run_refresh, force=False)
    return {"status": "started"}


@app.post("/api/reset")
def trigger_reset(background_tasks: BackgroundTasks):
    global refresh_in_progress
    if refresh_in_progress:
        return {"status": "already_running"}
    refresh_in_progress = True
    background_tasks.add_task(run_refresh, force=True)
    return {"status": "started"}


def run_refresh(force=False):
    global refresh_in_progress
    try:
        cmd = "python refresh.py --yes --force" if force else "python refresh.py --yes"
        subprocess.run(cmd, shell=True)
    finally:
        refresh_in_progress = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
