"""
Risk Metrics Module

Calculates three risk-adjusted return metrics for every ticker in the universe:
    1. Sharpe Ratio — absolute risk-adjusted return (annualised)
    2. Information Ratio (Universe) — excess return vs equal-weighted universe mean
    3. Information Ratio (Russell) — excess return vs IWM (Russell 2000 ETF)

Produces a weighted composite score and ranked output.

Usage:
    python -m src.signals.risk_metrics              # Run standalone
    python -m src.signals.risk_metrics --save        # Run and save to parquet
    python -m src.signals.risk_metrics --refresh     # Re-fetch benchmark data

Called by refresh.py as part of the daily pipeline.
"""

import os
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = "risk_metrics_config.json"

_FALLBACK_CONFIG = {
    "lookback_days": 63,
    "benchmark_ticker": "IWM",
    "benchmark_staleness_days": 1,
    "weights": {
        "sharpe": 0.34,
        "ir_universe": 0.33,
        "ir_russell": 0.33,
    },
}


def load_config():
    """Load risk metrics config, fall back to defaults if missing."""
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Strip notes key if present
        cfg.get("weights", {}).pop("notes", None)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        print("  Warning: risk_metrics_config.json not found, using defaults")
        return _FALLBACK_CONFIG.copy()


# ── Benchmark Fetcher ─────────────────────────────────────────────────────────

BENCHMARK_FILE = "data/benchmark_iwm.parquet"


def get_file_age_days(filepath):
    """Return file age in days, or None if missing."""
    if not os.path.exists(filepath):
        return None
    return (time.time() - os.path.getmtime(filepath)) / 86400


def fetch_benchmark(ticker="IWM", years=5, force=False):
    """
    Fetch daily OHLCV for the benchmark ETF from Polygon.
    Saves to its own parquet file, completely separate from the
    main price pipeline.

    Skips fetch if the file exists and is fresh (< staleness threshold).
    """
    cfg = load_config()
    staleness = cfg.get("benchmark_staleness_days", 1)

    if not force and os.path.exists(BENCHMARK_FILE):
        age = get_file_age_days(BENCHMARK_FILE)
        if age is not None and age < staleness:
            print(f"  Benchmark {ticker} is fresh ({age:.1f}d old), skipping fetch")
            return pd.read_parquet(BENCHMARK_FILE)

    print(f"  Fetching benchmark {ticker} from Polygon...")

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day"
           f"/{start_date}/{end_date}?adjusted=true&sort=asc"
           f"&limit=50000&apiKey={API_KEY}")

    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"  Error fetching {ticker}: HTTP {response.status_code}")
            # Fall back to cached file if available
            if os.path.exists(BENCHMARK_FILE):
                print(f"  Using cached benchmark data")
                return pd.read_parquet(BENCHMARK_FILE)
            return None

        data = response.json()
        results = data.get("results", [])

        if not results:
            print(f"  No data returned for {ticker}")
            if os.path.exists(BENCHMARK_FILE):
                return pd.read_parquet(BENCHMARK_FILE)
            return None

        df = pd.DataFrame(results)
        df = df.rename(columns={
            "t": "timestamp", "o": "open", "h": "high",
            "l": "low", "c": "close", "v": "volume",
        })
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
        df["date"] = pd.to_datetime(df["date"])
        df["ticker"] = ticker
        df = df[["date", "ticker", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("date").reset_index(drop=True)

        os.makedirs("data", exist_ok=True)
        df.to_parquet(BENCHMARK_FILE, index=False)
        print(f"  Saved {len(df)} rows to {BENCHMARK_FILE}")
        print(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")

        return df

    except Exception as e:
        print(f"  Error fetching benchmark: {e}")
        if os.path.exists(BENCHMARK_FILE):
            print(f"  Using cached benchmark data")
            return pd.read_parquet(BENCHMARK_FILE)
        return None


# ── Metric Calculations ───────────────────────────────────────────────────────

def compute_daily_returns(prices_df, tickers=None):
    """
    Compute daily close-to-close returns for each ticker.
    Returns a pivoted DataFrame: rows=dates, columns=tickers, values=returns.
    """
    df = prices_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    if tickers is not None:
        df = df[df["ticker"].isin(tickers)]

    # Pivot to wide format: date x ticker
    pivot = df.pivot_table(index="date", columns="ticker", values="close")
    pivot = pivot.sort_index()

    # Daily returns
    returns = pivot.pct_change(fill_method=None).dropna(how="all")

    return returns


def compute_sharpe(returns_df, lookback_days):
    """
    Compute annualised Sharpe ratio for each ticker.

    Sharpe = (mean daily return / std daily return) * sqrt(252)

    No risk-free rate subtracted — for a screener comparing stocks
    against each other, it's unnecessary and would just shift all
    values by the same constant.

    Args:
        returns_df: DataFrame with date index, ticker columns, return values
        lookback_days: number of trading days to use

    Returns:
        dict of {ticker: sharpe_ratio}
    """
    recent = returns_df.tail(lookback_days)
    results = {}

    for ticker in recent.columns:
        rets = recent[ticker].dropna()
        if len(rets) < lookback_days * 0.7:  # require 70% coverage
            continue

        mean_ret = rets.mean()
        std_ret = rets.std()

        if std_ret == 0 or np.isnan(std_ret):
            continue

        sharpe = (mean_ret / std_ret) * np.sqrt(252)
        results[ticker] = round(sharpe, 4)

    return results


def compute_ir_universe(returns_df, lookback_days):
    """
    Compute Information Ratio using the equal-weighted universe
    average return as the benchmark.

    For each day:
        benchmark_return = mean return across all tickers
        excess_return[ticker] = ticker_return - benchmark_return

    IR = mean(excess_returns) / std(excess_returns) * sqrt(252)

    Args:
        returns_df: DataFrame with date index, ticker columns, return values
        lookback_days: number of trading days to use

    Returns:
        dict of {ticker: ir_value}
    """
    recent = returns_df.tail(lookback_days)

    # Universe benchmark: equal-weighted mean return each day
    benchmark = recent.mean(axis=1)

    results = {}
    for ticker in recent.columns:
        rets = recent[ticker].dropna()

        # Align benchmark to the same dates as this ticker's valid returns
        aligned_bench = benchmark.loc[rets.index]

        if len(rets) < lookback_days * 0.7:
            continue

        excess = rets - aligned_bench
        mean_excess = excess.mean()
        tracking_error = excess.std()

        if tracking_error == 0 or np.isnan(tracking_error):
            continue

        ir = (mean_excess / tracking_error) * np.sqrt(252)
        results[ticker] = round(ir, 4)

    return results


def compute_ir_russell(returns_df, benchmark_df, lookback_days):
    """
    Compute Information Ratio using IWM (Russell 2000 ETF)
    as the benchmark.

    For each day:
        excess_return[ticker] = ticker_return - iwm_return

    IR = mean(excess_returns) / std(excess_returns) * sqrt(252)

    Args:
        returns_df: DataFrame with date index, ticker columns, return values
        benchmark_df: DataFrame with date, close columns for IWM
        lookback_days: number of trading days to use

    Returns:
        dict of {ticker: ir_value}
    """
    # Compute benchmark daily returns
    bench = benchmark_df.copy()
    bench["date"] = pd.to_datetime(bench["date"])
    bench = bench.sort_values("date").set_index("date")
    bench_returns = bench["close"].pct_change().dropna()

    recent = returns_df.tail(lookback_days)

    results = {}
    for ticker in recent.columns:
        rets = recent[ticker].dropna()

        # Align to common dates
        common_dates = rets.index.intersection(bench_returns.index)
        if len(common_dates) < lookback_days * 0.7:
            continue

        aligned_rets = rets.loc[common_dates]
        aligned_bench = bench_returns.loc[common_dates]

        excess = aligned_rets - aligned_bench
        mean_excess = excess.mean()
        tracking_error = excess.std()

        if tracking_error == 0 or np.isnan(tracking_error):
            continue

        ir = (mean_excess / tracking_error) * np.sqrt(252)
        results[ticker] = round(ir, 4)

    return results


# ── Composite Ranking ─────────────────────────────────────────────────────────

def rank_universe(sharpe_scores, ir_uni_scores, ir_russ_scores, weights):
    """
    Combine the three metrics into a weighted composite and rank.

    Each metric is first converted to a percentile rank (0-100) within
    the universe, then blended using the configured weights.

    Returns a DataFrame with all metrics, composite score, and rank.
    """
    # Build combined DataFrame
    all_tickers = set(sharpe_scores.keys()) | set(ir_uni_scores.keys()) | set(ir_russ_scores.keys())

    rows = []
    for ticker in all_tickers:
        rows.append({
            "ticker": ticker,
            "sharpe": sharpe_scores.get(ticker),
            "ir_universe": ir_uni_scores.get(ticker),
            "ir_russell": ir_russ_scores.get(ticker),
        })

    df = pd.DataFrame(rows)

    # Require at least 2 of 3 metrics to be present
    valid_count = df[["sharpe", "ir_universe", "ir_russell"]].notna().sum(axis=1)
    df = df[valid_count >= 2].copy()

    if df.empty:
        return pd.DataFrame()

    # Convert each metric to percentile rank (0-100)
    for col in ["sharpe", "ir_universe", "ir_russell"]:
        df[f"{col}_pctile"] = df[col].rank(pct=True) * 100

    # Weighted composite using percentile scores
    w_sharpe = weights.get("sharpe", 0.34)
    w_ir_uni = weights.get("ir_universe", 0.33)
    w_ir_russ = weights.get("ir_russell", 0.33)

    def weighted_composite(row):
        total_score = 0
        total_weight = 0

        if pd.notna(row.get("sharpe_pctile")):
            total_score += row["sharpe_pctile"] * w_sharpe
            total_weight += w_sharpe
        if pd.notna(row.get("ir_universe_pctile")):
            total_score += row["ir_universe_pctile"] * w_ir_uni
            total_weight += w_ir_uni
        if pd.notna(row.get("ir_russell_pctile")):
            total_score += row["ir_russell_pctile"] * w_ir_russ
            total_weight += w_ir_russ

        return total_score / total_weight if total_weight > 0 else np.nan

    df["composite"] = df.apply(weighted_composite, axis=1)
    df = df.dropna(subset=["composite"])
    df["rank"] = df["composite"].rank(ascending=False).astype(int)
    df = df.sort_values("rank").reset_index(drop=True)

    # Keep raw values + percentiles + composite + rank
    output_cols = [
        "ticker", "rank", "composite",
        "sharpe", "sharpe_pctile",
        "ir_universe", "ir_universe_pctile",
        "ir_russell", "ir_russell_pctile",
    ]
    return df[[c for c in output_cols if c in df.columns]]


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_risk_metrics(save=False, refresh_benchmark=False):
    """
    Full pipeline: fetch benchmark, compute all three metrics, rank, save.
    """
    cfg = load_config()
    lookback = cfg["lookback_days"]
    benchmark_ticker = cfg.get("benchmark_ticker", "IWM")
    weights = cfg["weights"]

    print(f"\n=== Risk Metrics ===")
    print(f"  Lookback: {lookback} trading days (~{lookback/21:.0f} months)")
    print(f"  Benchmark: {benchmark_ticker}")
    print(f"  Weights: {', '.join(f'{k}: {v:.0%}' for k, v in weights.items())}\n")

    # Step 1: Load universe and prices
    print("--- Loading data ---\n")
    if not os.path.exists("data/universe.parquet"):
        print("  Error: universe.parquet not found. Run universe.py first.")
        return None
    if not os.path.exists("data/prices_combined.parquet"):
        print("  Error: prices_combined.parquet not found. Run fetch_prices.py first.")
        return None

    universe = pd.read_parquet("data/universe.parquet")
    prices = pd.read_parquet("data/prices_combined.parquet")
    tickers = universe["ticker"].tolist()
    print(f"  Universe: {len(tickers)} tickers")
    print(f"  Prices: {len(prices)} rows\n")

    # Step 2: Fetch benchmark
    print("--- Benchmark ---\n")
    benchmark = fetch_benchmark(benchmark_ticker, force=refresh_benchmark)
    if benchmark is None:
        print("  Warning: no benchmark data, IR-Russell will be skipped")

    # Step 3: Compute daily returns
    print("\n--- Computing daily returns ---\n")
    returns_df = compute_daily_returns(prices, tickers)
    print(f"  Returns matrix: {returns_df.shape[0]} days x {returns_df.shape[1]} tickers")
    print(f"  Date range: {returns_df.index.min().date()} to {returns_df.index.max().date()}")

    if len(returns_df) < lookback:
        print(f"  Warning: only {len(returns_df)} days available, need {lookback}")

    # Step 4: Compute metrics
    print(f"\n--- Computing metrics (lookback={lookback}d) ---\n")

    print("  Computing Sharpe ratios...")
    sharpe_scores = compute_sharpe(returns_df, lookback)
    print(f"    Scored {len(sharpe_scores)} tickers")

    print("  Computing IR (universe benchmark)...")
    ir_uni_scores = compute_ir_universe(returns_df, lookback)
    print(f"    Scored {len(ir_uni_scores)} tickers")

    ir_russ_scores = {}
    if benchmark is not None:
        print(f"  Computing IR ({benchmark_ticker} benchmark)...")
        ir_russ_scores = compute_ir_russell(returns_df, benchmark, lookback)
        print(f"    Scored {len(ir_russ_scores)} tickers")
    else:
        print("  Skipping IR-Russell (no benchmark data)")

    # Step 5: Rank
    print(f"\n--- Ranking ---\n")
    ranked = rank_universe(sharpe_scores, ir_uni_scores, ir_russ_scores, weights)

    if ranked.empty:
        print("  No results. Check data coverage.")
        return None

    print(f"  Ranked {len(ranked)} tickers")

    # Merge names for display
    ranked = ranked.merge(
        universe[["ticker", "name"]],
        on="ticker",
        how="left"
    )
    ranked["name"] = ranked["name"].fillna("").astype(str)

    # Display top 20
    print(f"\n{'Rank':<6}{'Ticker':<8}{'Comp':>7}{'Sharpe':>8}{'IR-Uni':>8}{'IR-Russ':>9}  {'Name'}")
    print("-" * 80)

    for _, row in ranked.head(20).iterrows():
        sharpe_str = f"{row['sharpe']:+.2f}" if pd.notna(row.get("sharpe")) else "N/A"
        ir_uni_str = f"{row['ir_universe']:+.2f}" if pd.notna(row.get("ir_universe")) else "N/A"
        ir_russ_str = f"{row['ir_russell']:+.2f}" if pd.notna(row.get("ir_russell")) else "N/A"
        name = str(row.get("name", ""))[:28]
        print(f"{row['rank']:<6}{row['ticker']:<8}{row['composite']:>7.1f}"
              f"{sharpe_str:>8}{ir_uni_str:>8}{ir_russ_str:>9}  {name}")

    # Summary stats
    print(f"\nScore distribution:")
    print(f"  Sharpe — mean: {ranked['sharpe'].mean():.2f}, "
          f"median: {ranked['sharpe'].median():.2f}, "
          f"std: {ranked['sharpe'].std():.2f}")
    if "ir_universe" in ranked.columns:
        print(f"  IR-Uni — mean: {ranked['ir_universe'].mean():.2f}, "
              f"median: {ranked['ir_universe'].median():.2f}, "
              f"std: {ranked['ir_universe'].std():.2f}")
    if "ir_russell" in ranked.columns and ranked["ir_russell"].notna().any():
        print(f"  IR-Russ — mean: {ranked['ir_russell'].mean():.2f}, "
              f"median: {ranked['ir_russell'].median():.2f}, "
              f"std: {ranked['ir_russell'].std():.2f}")

    # Save
    if save:
        os.makedirs("data", exist_ok=True)
        # Drop the name column before saving (will be re-merged by the API)
        save_df = ranked.drop(columns=["name"], errors="ignore")
        save_df.to_parquet("data/risk_metrics.parquet", index=False)
        print(f"\n  Saved to data/risk_metrics.parquet ({len(save_df)} tickers)")

    return ranked


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compute risk-adjusted return metrics")
    parser.add_argument("--save", action="store_true",
                        help="Save results to data/risk_metrics.parquet")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-fetch of benchmark data")
    parser.add_argument("--top", type=int, default=20,
                        help="Number of top stocks to display (default: 20)")
    args = parser.parse_args()

    run_risk_metrics(save=args.save, refresh_benchmark=args.refresh)


if __name__ == "__main__":
    main()
