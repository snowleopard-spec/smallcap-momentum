"""
Signal Runner

Orchestrates all signals, applies configurable weights,
and produces a final ranked watchlist.

Signals:
    1. price_momentum     - Composite 3/6/12 month price returns
    2. volume_surge       - Volume spike relative to history
    3. price_acceleration - Momentum of momentum (2nd derivative)
    4. rsi                - Relative Strength Index (momentum-adjusted)
    5. stochastic         - Slow Stochastic Oscillator
    6. financial_health   - SEC fundamentals solvency score
    7. news_attention     - Media coverage level and surge
    8. insider_activity   - Insider buying/selling from Form 4 filings
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime

# Import all signal classes
from src.signals.price_momentum import PriceMomentum
from src.signals.volume_surge import VolumeSurge
from src.signals.price_acceleration import PriceAcceleration
from src.signals.rsi import RSISignal
from src.signals.stochastic import StochasticSignal
from src.signals.financial_health import FinancialHealth
from src.signals.news_attention import NewsAttention
from src.signals.insider_activity import InsiderActivity

# Default weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "price_momentum": 0.20,
    "volume_surge": 0.12,
    "price_acceleration": 0.12,
    "rsi": 0.08,
    "stochastic": 0.08,
    "financial_health": 0.15,
    "news_attention": 0.10,
    "insider_activity": 0.15,
}

# Price-based signal classes (standard constructor)
PRICE_SIGNAL_CLASSES = {
    "price_momentum": PriceMomentum,
    "volume_surge": VolumeSurge,
    "price_acceleration": PriceAcceleration,
    "rsi": RSISignal,
    "stochastic": StochasticSignal,
}


def run_all_signals(prices_df, universe_df, fundamentals_df=None,
                    news_df=None, insider_df=None):
    """
    Run all signals and return individual score DataFrames.
    """
    results = {}

    # Run price-based signals
    for name, signal_class in PRICE_SIGNAL_CLASSES.items():
        print(f"  Running {name}...")
        signal = signal_class(prices_df, universe_df)
        scores = signal.score()
        results[name] = scores
        print(f"    Scored {len(scores)} tickers")

    # Run financial health signal
    print(f"  Running financial_health...")
    if fundamentals_df is not None and not fundamentals_df.empty:
        signal = FinancialHealth(prices_df, universe_df, fundamentals_df)
        scores = signal.score()
        results["financial_health"] = scores
        print(f"    Scored {len(scores)} tickers")
    else:
        print(f"    Skipped (no fundamentals data)")

    # Run news attention signal
    print(f"  Running news_attention...")
    if news_df is not None and not news_df.empty:
        signal = NewsAttention(prices_df, universe_df, news_df)
        scores = signal.score()
        results["news_attention"] = scores
        print(f"    Scored {len(scores)} tickers")
    else:
        print(f"    Skipped (no news data)")

    # Run insider activity signal
    print(f"  Running insider_activity...")
    if insider_df is not None and not insider_df.empty:
        signal = InsiderActivity(prices_df, universe_df, insider_df)
        scores = signal.score()
        results["insider_activity"] = scores
        print(f"    Scored {len(scores)} tickers")
    else:
        print(f"    Skipped (no insider data)")

    return results


def combine_scores(signal_results, weights=None):
    """
    Combine individual signal scores into a composite score.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    # Validate weights sum to 1
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 0.01:
        print(f"  Warning: weights sum to {weight_sum}, normalising to 1.0")
        weights = {k: v / weight_sum for k, v in weights.items()}

    # Pivot each signal's scores into columns
    merged = None
    for signal_name, scores_df in signal_results.items():
        if scores_df.empty:
            continue

        signal_scores = scores_df[["ticker", "score"]].rename(
            columns={"score": signal_name}
        )

        if merged is None:
            merged = signal_scores
        else:
            merged = merged.merge(signal_scores, on="ticker", how="outer")

    if merged is None or merged.empty:
        return pd.DataFrame()

    signal_columns = [name for name in weights.keys() if name in merged.columns]

    def weighted_score(row):
        total_weight = 0
        total_score = 0
        for col in signal_columns:
            if pd.notna(row[col]):
                total_score += row[col] * weights[col]
                total_weight += weights[col]
        if total_weight == 0:
            return np.nan
        return total_score / total_weight

    merged["composite_score"] = merged.apply(weighted_score, axis=1)
    merged = merged.dropna(subset=["composite_score"])
    merged["rank"] = merged["composite_score"].rank(ascending=False).astype(int)
    merged = merged.sort_values("rank")

    return merged


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run momentum signals")
    parser.add_argument("--top", type=int, default=20,
                        help="Number of top stocks to display (default: 20)")
    parser.add_argument("--min-score", type=float, default=None,
                        help="Only show stocks above this composite score")
    parser.add_argument("--weight-momentum", type=float, default=DEFAULT_WEIGHTS["price_momentum"])
    parser.add_argument("--weight-volume", type=float, default=DEFAULT_WEIGHTS["volume_surge"])
    parser.add_argument("--weight-acceleration", type=float, default=DEFAULT_WEIGHTS["price_acceleration"])
    parser.add_argument("--weight-rsi", type=float, default=DEFAULT_WEIGHTS["rsi"])
    parser.add_argument("--weight-stochastic", type=float, default=DEFAULT_WEIGHTS["stochastic"])
    parser.add_argument("--weight-health", type=float, default=DEFAULT_WEIGHTS["financial_health"])
    parser.add_argument("--weight-news", type=float, default=DEFAULT_WEIGHTS["news_attention"])
    parser.add_argument("--weight-insider", type=float, default=DEFAULT_WEIGHTS["insider_activity"])
    parser.add_argument("--save", action="store_true",
                        help="Save results to data/watchlist.parquet")
    args = parser.parse_args()

    weights = {
        "price_momentum": args.weight_momentum,
        "volume_surge": args.weight_volume,
        "price_acceleration": args.weight_acceleration,
        "rsi": args.weight_rsi,
        "stochastic": args.weight_stochastic,
        "financial_health": args.weight_health,
        "news_attention": args.weight_news,
        "insider_activity": args.weight_insider,
    }

    print(f"=== Signal Runner ===")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Weights: {', '.join(f'{k}: {v:.0%}' for k, v in weights.items())}\n")

    # Load data
    print("--- Loading data ---\n")
    prices = pd.read_parquet("data/prices_combined.parquet")

    # Load universe and apply a defensive market cap filter using config.json.
    # This is the safety net: even if universe.parquet has stale entries,
    # they are stripped here before any signal runs.
    universe = pd.read_parquet("data/universe.parquet")
    try:
        with open("config.json") as f:
            cfg = json.load(f)
        min_cap = cfg["universe"]["min_market_cap"]
        max_cap = cfg["universe"]["max_market_cap"]
        before = len(universe)
        universe = universe[
            (universe["market_cap"] >= min_cap) &
            (universe["market_cap"] <= max_cap)
        ].reset_index(drop=True)
        after = len(universe)
        if before != after:
            print(f"  [Cap filter] Removed {before - after} tickers outside "
                  f"${min_cap/1e6:.0f}M–${max_cap/1e6:.0f}M bounds")
    except (FileNotFoundError, KeyError) as e:
        print(f"  Warning: could not apply cap filter from config.json ({e})")

    print(f"  Prices: {len(prices)} rows")
    print(f"  Universe: {len(universe)} tickers")
    print(f"  Date range: {prices['date'].min().date()} to {prices['date'].max().date()}")

    fundamentals = None
    if os.path.exists("data/fundamentals.parquet"):
        fundamentals = pd.read_parquet("data/fundamentals.parquet")
        print(f"  Fundamentals: {len(fundamentals)} tickers")

    news = None
    if os.path.exists("data/news_attention.parquet"):
        news = pd.read_parquet("data/news_attention.parquet")
        print(f"  News: {len(news)} tickers")

    insider = None
    if os.path.exists("data/insider_activity.parquet"):
        insider = pd.read_parquet("data/insider_activity.parquet")
        print(f"  Insider: {len(insider)} tickers")

    print()

    # Run signals
    print("--- Running signals ---\n")
    signal_results = run_all_signals(prices, universe, fundamentals, news, insider)

    # Combine scores
    print(f"\n--- Combining scores ---\n")
    watchlist = combine_scores(signal_results, weights)

    if watchlist.empty:
        print("No results. Check your data.")
        return

    # Add company names and market caps
    watchlist = watchlist.merge(
        universe[["ticker", "name", "market_cap"]],
        on="ticker",
        how="left"
    )

    # Display results
    print(f"Total scored: {len(watchlist)} stocks\n")

    display = watchlist.head(args.top) if args.min_score is None else \
              watchlist[watchlist["composite_score"] >= args.min_score]

    signal_cols = ["price_momentum", "volume_surge", "price_acceleration",
                   "rsi", "stochastic", "financial_health", "news_attention",
                   "insider_activity"]
    short_names = {
        "price_momentum": "Momntm",
        "volume_surge": "Volume",
        "price_acceleration": "Accel",
        "rsi": "RSI",
        "stochastic": "Stoch",
        "financial_health": "Health",
        "news_attention": "News",
        "insider_activity": "Insdr",
    }

    header = f"{'Rank':<6}{'Ticker':<8}{'Comp':>6}"
    for col in signal_cols:
        header += f"{short_names[col]:>7}"
    header += f"  {'Name'}"
    print(header)
    print("-" * 125)

    for _, row in display.iterrows():
        line = f"{row['rank']:<6}{row['ticker']:<8}{row['composite_score']:>6.1f}"
        for col in signal_cols:
            val = row.get(col, np.nan)
            line += f"{val:>7.1f}" if pd.notna(val) else f"{'N/A':>7}"
        line += f"  {row.get('name', '')[:30]}"
        print(line)

    # Summary stats
    print(f"\nScore distribution:")
    print(f"  Mean:   {watchlist['composite_score'].mean():.1f}")
    print(f"  Median: {watchlist['composite_score'].median():.1f}")
    print(f"  Std:    {watchlist['composite_score'].std():.1f}")
    print(f"  >70:    {len(watchlist[watchlist['composite_score'] > 70])} stocks")
    print(f"  >80:    {len(watchlist[watchlist['composite_score'] > 80])} stocks")
    print(f"  >90:    {len(watchlist[watchlist['composite_score'] > 90])} stocks")

    # Save if requested
    if args.save:
        os.makedirs("data", exist_ok=True)
        watchlist.to_parquet("data/watchlist.parquet", index=False)
        print(f"\nSaved to data/watchlist.parquet")


if __name__ == "__main__":
    main()
