"""
Composite Price Momentum Signal

Measures the stock's return over multiple lookback periods and blends them.
Uses 3-month, 6-month, and 12-month returns (excluding the most recent
month to avoid short-term mean reversion).

A stock ranking highly across all three periods is showing persistent
upward strength, not just a short-term bounce.

Raw signal = weighted average of period returns
    - 40% weight to 3-month return (recent momentum)
    - 30% weight to 6-month return (medium-term trend)
    - 30% weight to 12-month return (long-term trend)

All returns skip the most recent 21 trading days (~1 month)
to avoid the well-documented short-term reversal effect.
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class PriceMomentum(BaseSignal):

    @property
    def name(self):
        return "price_momentum"

    @property
    def description(self):
        return "Composite 3/6/12 month price momentum (skipping most recent month)"

    def calculate_raw(self):
        """
        For each ticker, calculate blended momentum score.

        Lookback periods (in trading days):
            - 3 month:  ~63 trading days
            - 6 month:  ~126 trading days
            - 12 month: ~252 trading days
            - Skip period: ~21 trading days (most recent month)
        """
        SKIP_DAYS = 21
        PERIODS = {
            "mom_3m": {"days": 63, "weight": 0.4},
            "mom_6m": {"days": 126, "weight": 0.3},
            "mom_12m": {"days": 252, "weight": 0.3},
        }

        results = []
        tickers = self.prices["ticker"].unique()

        for ticker in tickers:
            df = self.get_ticker_prices(ticker)

            if len(df) < 252 + SKIP_DAYS:
                # Not enough history for 12-month momentum
                continue

            # Price at the skip point (21 days ago)
            price_recent = df.iloc[-(SKIP_DAYS + 1)]["close"]

            if price_recent <= 0:
                continue

            # Calculate return for each period
            period_returns = {}
            valid = True

            for period_name, config in PERIODS.items():
                lookback = config["days"]
                idx = -(SKIP_DAYS + lookback)

                if abs(idx) > len(df):
                    valid = False
                    break

                price_past = df.iloc[idx]["close"]
                if price_past <= 0:
                    valid = False
                    break

                period_return = (price_recent - price_past) / price_past
                period_returns[period_name] = period_return

            if not valid:
                continue

            # Weighted blend
            raw_signal = sum(
                period_returns[name] * config["weight"]
                for name, config in PERIODS.items()
            )

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "mom_3m": period_returns["mom_3m"],
                "mom_6m": period_returns["mom_6m"],
                "mom_12m": period_returns["mom_12m"],
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = PriceMomentum(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest bullish momentum):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print("\nBottom 10 (strongest bearish momentum):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
