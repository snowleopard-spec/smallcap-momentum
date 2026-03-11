"""
Volume Surge Signal

Measures whether a stock is experiencing unusually high trading volume
relative to its own history, normalised by market cap.

This captures "conviction" — when real money is flowing into a stock.
A breakout on 3x normal volume is much more significant than one on
average volume.

Raw signal combines two components:
    1. Volume ratio: recent 5-day average volume vs 60-day average volume
       (how much more active is this stock than usual?)
    2. Turnover: recent 5-day volume as a percentage of market cap
       (how much of the company changed hands?)

The signal is directional: volume surge on an up move is bullish (+),
volume surge on a down move is bearish (-).

Raw signal = volume_ratio * turnover * price_direction
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class VolumeSurge(BaseSignal):

    @property
    def name(self):
        return "volume_surge"

    @property
    def description(self):
        return "Volume surge relative to history, direction-adjusted and market cap normalised"

    def calculate_raw(self):
        """
        For each ticker, calculate the direction-adjusted volume surge.

        Components:
            - volume_ratio: 5-day avg volume / 60-day avg volume
            - turnover: (5-day total volume * vwap) / market_cap
            - direction: sign of 5-day price return
        """
        SHORT_WINDOW = 5      # Recent volume window (1 week)
        LONG_WINDOW = 60      # Historical baseline (3 months)
        MIN_HISTORY = 60      # Minimum days of data required

        results = []
        tickers = self.prices["ticker"].unique()

        for ticker in tickers:
            df = self.get_ticker_prices(ticker)

            if len(df) < MIN_HISTORY:
                continue

            # Recent and historical volume
            recent = df.tail(SHORT_WINDOW)
            historical = df.tail(LONG_WINDOW)

            avg_vol_recent = recent["volume"].mean()
            avg_vol_historical = historical["volume"].mean()

            if avg_vol_historical <= 0 or avg_vol_recent <= 0:
                continue

            # Volume ratio: how elevated is recent volume?
            volume_ratio = avg_vol_recent / avg_vol_historical

            # Turnover: volume relative to market cap
            market_cap = self.get_market_cap(ticker)
            if market_cap is None or market_cap <= 0:
                continue

            # Use closing price * volume as dollar volume
            recent_dollar_volume = (recent["close"] * recent["volume"]).sum()
            turnover = recent_dollar_volume / market_cap

            # Price direction over the short window
            price_start = recent.iloc[0]["close"]
            price_end = recent.iloc[-1]["close"]

            if price_start <= 0:
                continue

            price_return = (price_end - price_start) / price_start
            direction = np.sign(price_return)

            # If price is flat, use a small positive bias
            # (elevated volume with flat price is mildly bullish — accumulation)
            if direction == 0:
                direction = 0.5

            # Combine: volume_ratio * turnover * direction
            # volume_ratio captures relative surge
            # turnover captures absolute significance
            # direction makes it bullish or bearish
            raw_signal = volume_ratio * turnover * direction

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "volume_ratio": round(volume_ratio, 2),
                "turnover": round(turnover, 4),
                "price_return_5d": round(price_return, 4),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = VolumeSurge(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest bullish volume surge):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    print("\nBottom 10 (strongest bearish volume surge):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
