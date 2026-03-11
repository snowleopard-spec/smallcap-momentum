"""
Signal Runner

Orchestrates all signals, applies configurable weights,
and produces a final ranked watchlist.

Each signal produces a 0-100 percentile score.
The runner combines them into a single composite score
using the specified weights, then ranks the universe.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime

# Import all signal classes
from src.signals.price_momentum import PriceMomentum
from src.signals.volume_surge import VolumeSurge
from src.signals.price_acceleration import PriceAcceleration
from src.signals.rsi import RSISignal
from src.signals.stochastic import StochasticSignal

# Default weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "price_momentum": 0.30,
    "volume_surge": 0.20,
    "price_acceleration": 0.20,
    "rsi": 0.15,
    "stochastic": 0.15,
}

# All available signal classes
SIGNAL_CLASSES = {
    "price_momentum": PriceMomentum,
    "volume_surge": VolumeSurge,
    "price_acceleration": PriceAcceleration,
    "rsi": RSISignal,
    "stochastic": StochasticSignal,
}


def run_all_signals(prices_df, universe_df):
    """
    Run all signals and return individual score DataFrames.

    Returns:
        dict of signal_name -> DataFrame with columns:
        ticker, signal_name, raw_signal, score
    """
    results = {}

    for name, signal_class in SIGNAL_CLASSES.items():
        print(f"  Running {name}...")
        signal = signal_class(prices_df, universe_df)
        scores = signal.score()
        results[name] = scores
        print(f"    Scored {len(scores)} tickers")

    return results


def combine_scores(signal_results, weights=None):
    """
    Combine individual signal scores into a composite score.

    Args:
        signal_results: dict of signal_name -> score DataFrame
        weights: dict of signal_name -> weight (must sum to 1.0)

    Returns:
        DataFrame with ticker, individual scores, and composite score
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

        # Rename score column to signal name
        signal_scores = scores_df[["ticker", "score"]].rename(
            columns={"score": signal_name}
        )

        if merged is None:
            merged = signal_scores
        else:
            merged = merged.merge(signal_scores, on="ticker", how="outer")

    if merged is None or merged.empty:
        return pd.DataFrame()

    # Calculate composite score (weighted average of available signals)
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
        # Normalise by actual weight used (handles missing signals)
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
    parser.add_argument("--weight-momentum", type=float, default=DEFAULT_WEIGHTS["price_momentum"],
                        help=f"Weight for price momentum (default: {DEFAULT_WEIGHTS['price_momentum']})")
    parser.add_argument("--weight-volume", type=float, default=DEFAULT_WEIGHTS["volume_surge"],
                        help=f"Weight for volume surge (default: {DEFAULT_WEIGHTS['volume_surge']})")
    parser.add_argument("--weight-acceleration", type=float, default=DEFAULT_WEIGHTS["price_acceleration"],
                        help=f"Weight for price acceleration (default: {DEFAULT_WEIGHTS['price_acceleration']})")
    parser.add_argument("--weight-rsi", type=float, default=DEFAULT_WEIGHTS["rsi"],
                        help=f"Weight for RSI (default: {DEFAULT_WEIGHTS['rsi']})")
    parser.add_argument("--weight-stochastic", type=float, default=DEFAULT_WEIGHTS["stochastic"],
                        help=f"Weight for stochastic (default: {DEFAULT_WEIGHTS['stochastic']})")
    parser.add_argument("--save", action="store_true",
                        help="Save results to data/watchlist.parquet")
    args = parser.parse_args()

    weights = {
        "price_momentum": args.weight_momentum,
        "volume_surge": args.weight_volume,
        "price_acceleration": args.weight_acceleration,
        "rsi": args.weight_rsi,
        "stochastic": args.weight_stochastic,
    }

    print(f"=== Signal Runner ===")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Weights: {', '.join(f'{k}: {v:.0%}' for k, v in weights.items())}\n")

    # Load data
    print("--- Loading data ---\n")
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")
    print(f"  Prices: {len(prices)} rows")
    print(f"  Universe: {len(universe)} tickers")
    print(f"  Date range: {prices['date'].min().date()} to {prices['date'].max().date()}\n")

    # Run signals
    print("--- Running signals ---\n")
    signal_results = run_all_signals(prices, universe)

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

    signal_cols = ["price_momentum", "volume_surge", "price_acceleration", "rsi", "stochastic"]

    header = f"{'Rank':<6}{'Ticker':<8}{'Composite':>10}"
    for col in signal_cols:
        short_name = {"price_momentum": "Momentum", "volume_surge": "Volume",
                      "price_acceleration": "Accel", "rsi": "RSI",
                      "stochastic": "Stoch"}[col]
        header += f"{short_name:>9}"
    header += f"  {'Name'}"
    print(header)
    print("-" * 110)

    for _, row in display.iterrows():
        line = f"{row['rank']:<6}{row['ticker']:<8}{row['composite_score']:>10.1f}"
        for col in signal_cols:
            val = row.get(col, np.nan)
            line += f"{val:>9.1f}" if pd.notna(val) else f"{'N/A':>9}"
        line += f"  {row.get('name', '')[:35]}"
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
