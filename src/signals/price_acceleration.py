"""
Price Acceleration Signal

Measures whether a stock's momentum is increasing or decreasing.
This is the "second derivative" — the rate of change of the rate of change.

A stock with steady 2% monthly returns has momentum but no acceleration.
A stock going from 1% to 3% to 5% monthly returns is accelerating.
This catches stocks EARLY in their move, before they show up on
simple momentum screens.

Raw signal = short-term momentum minus long-term momentum

If short-term momentum > long-term momentum, the stock is accelerating.
If short-term < long-term, the stock is decelerating (even if still positive).

This is similar in spirit to MACD (Moving Average Convergence Divergence)
but applied to returns rather than price levels.
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class PriceAcceleration(BaseSignal):

    @property
    def name(self):
        return "price_acceleration"

    @property
    def description(self):
        return "Price momentum acceleration (short-term momentum vs long-term momentum)"

    def calculate_raw(self):
        """
        For each ticker, calculate the acceleration of price momentum.

        Method:
            1. Calculate 21-day (1 month) rolling returns
            2. Compare the most recent 1-month return to the average
               monthly return over the past 6 months
            3. Positive difference = accelerating momentum
               Negative difference = decelerating momentum

        Also incorporates a smoothed acceleration using exponential
        moving averages of returns to reduce noise.
        """
        SHORT_PERIOD = 21     # Recent momentum window (~1 month)
        LONG_PERIOD = 126     # Baseline momentum window (~6 months)
        MIN_HISTORY = 150     # Minimum days needed

        results = []
        tickers = self.prices["ticker"].unique()

        for ticker in tickers:
            df = self.get_ticker_prices(ticker)

            if len(df) < MIN_HISTORY:
                continue

            close = df["close"].values

            if close[-1] <= 0 or close[-(SHORT_PERIOD + 1)] <= 0:
                continue

            # Recent 1-month return
            recent_return = (close[-1] - close[-(SHORT_PERIOD + 1)]) / close[-(SHORT_PERIOD + 1)]

            # Calculate rolling 1-month returns over the past 6 months
            # to get the average monthly return as our baseline
            monthly_returns = []
            for i in range(1, 7):  # 6 non-overlapping months
                end_idx = -(i * SHORT_PERIOD)
                start_idx = -((i + 1) * SHORT_PERIOD)

                if abs(start_idx) > len(close):
                    break

                # Handle negative indexing carefully
                end_val = close[end_idx] if end_idx != 0 else close[-1]
                start_val = close[start_idx]

                if start_val <= 0:
                    continue

                monthly_ret = (end_val - start_val) / start_val
                monthly_returns.append(monthly_ret)

            if len(monthly_returns) < 3:
                continue

            avg_monthly_return = np.mean(monthly_returns)

            # Acceleration = recent return minus average historical return
            acceleration = recent_return - avg_monthly_return

            # Also calculate a smoothed measure using EMA of daily returns
            daily_returns = pd.Series(close).pct_change().dropna()

            if len(daily_returns) < LONG_PERIOD:
                continue

            # Short EMA of returns (responsive)
            ema_short = daily_returns.tail(LONG_PERIOD).ewm(span=SHORT_PERIOD).mean().iloc[-1]

            # Long EMA of returns (slow)
            ema_long = daily_returns.tail(LONG_PERIOD).ewm(span=LONG_PERIOD).mean().iloc[-1]

            # EMA crossover as secondary acceleration measure
            ema_acceleration = ema_short - ema_long

            # Blend the two measures (equal weight)
            raw_signal = (acceleration + ema_acceleration * 100) / 2

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "recent_1m_return": round(recent_return, 4),
                "avg_monthly_return": round(avg_monthly_return, 4),
                "acceleration": round(acceleration, 4),
                "ema_acceleration": round(ema_acceleration, 6),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = PriceAcceleration(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest acceleration):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    print("\nBottom 10 (strongest deceleration):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
