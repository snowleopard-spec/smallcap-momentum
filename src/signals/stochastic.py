"""
Stochastic Oscillator Signal

The Stochastic Oscillator measures where a stock's closing price sits
relative to its high-low range over a lookback period. It answers:
"Is the stock closing near its highs or near its lows?"

Components:
    %K = (Close - Lowest Low) / (Highest High - Lowest Low) * 100
    %D = 3-day SMA of %K (smoothed signal line)

Traditional interpretation:
    - %K > 80 = overbought
    - %K < 20 = oversold

For momentum screening, we care about:
    1. Where %K is (above 50 = bullish, closing near highs)
    2. The %K/%D crossover (bullish when %K crosses above %D)
    3. Trend context: high stochastic in an uptrend = strong momentum

We use the "slow stochastic" (smoothed %K) which is less noisy
than the raw fast stochastic.

Standard periods: %K = 14 days, %D smoothing = 3 days, slow smoothing = 3 days
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class StochasticSignal(BaseSignal):

    @property
    def name(self):
        return "stochastic"

    @property
    def description(self):
        return "Slow Stochastic Oscillator (14,3,3) with crossover detection"

    def calculate_stochastic(self, df, k_period=14, d_period=3, slow_period=3):
        """
        Calculate slow stochastic oscillator.

        Returns DataFrame with columns: fast_k, slow_k, slow_d
        """
        # Fast %K
        lowest_low = df["low"].rolling(window=k_period).min()
        highest_high = df["high"].rolling(window=k_period).max()

        range_hl = highest_high - lowest_low
        # Avoid division by zero
        range_hl = range_hl.replace(0, np.nan)

        fast_k = ((df["close"] - lowest_low) / range_hl) * 100

        # Slow %K = SMA of Fast %K
        slow_k = fast_k.rolling(window=slow_period).mean()

        # Slow %D = SMA of Slow %K
        slow_d = slow_k.rolling(window=d_period).mean()

        return pd.DataFrame({
            "fast_k": fast_k,
            "slow_k": slow_k,
            "slow_d": slow_d,
        })

    def calculate_raw(self):
        """
        For each ticker, calculate momentum-adjusted stochastic signal.

        Raw signal combines:
            1. Level: where slow %K is (centred at 50)
            2. Crossover: whether %K is above or below %D
            3. Trend: whether %K is rising or falling

        Weighting:
            - 50% level (where is price in its range)
            - 30% crossover (%K vs %D spread)
            - 20% trend (direction of %K)
        """
        K_PERIOD = 14
        D_PERIOD = 3
        SLOW_PERIOD = 3
        MIN_HISTORY = 30

        results = []
        tickers = self.prices["ticker"].unique()

        for ticker in tickers:
            df = self.get_ticker_prices(ticker).reset_index(drop=True)

            if len(df) < MIN_HISTORY:
                continue

            stoch = self.calculate_stochastic(df, K_PERIOD, D_PERIOD, SLOW_PERIOD)

            current_k = stoch["slow_k"].iloc[-1]
            current_d = stoch["slow_d"].iloc[-1]
            prev_k = stoch["slow_k"].iloc[-2]

            if pd.isna(current_k) or pd.isna(current_d) or pd.isna(prev_k):
                continue

            # Component 1: Level (where is %K relative to 50)
            # Scale: -1 to +1
            level_signal = (current_k - 50) / 50

            # Component 2: Crossover (%K - %D spread)
            # Positive = %K above %D (bullish)
            # Normalise by dividing by typical spread magnitude
            crossover_signal = (current_k - current_d) / 20
            crossover_signal = np.clip(crossover_signal, -1, 1)

            # Component 3: Trend (is %K rising or falling)
            k_change = current_k - prev_k
            # Normalise: typical daily change is ~2-5 points
            trend_signal = k_change / 10
            trend_signal = np.clip(trend_signal, -1, 1)

            # Weighted combination
            raw_signal = (
                0.50 * level_signal +
                0.30 * crossover_signal +
                0.20 * trend_signal
            )

            # Get 5-day average for additional context
            avg_k_5d = stoch["slow_k"].tail(5).mean()

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "slow_k": round(current_k, 1),
                "slow_d": round(current_d, 1),
                "k_minus_d": round(current_k - current_d, 1),
                "k_5d_avg": round(avg_k_5d, 1),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = StochasticSignal(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest bullish stochastic):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print("\nBottom 10 (strongest bearish stochastic):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
