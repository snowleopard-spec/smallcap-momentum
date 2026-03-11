"""
Relative Strength Index (RSI) Signal

RSI measures the speed and magnitude of recent price changes to evaluate
whether a stock is overbought or oversold. It oscillates between 0 and 100.

Traditional interpretation:
    - RSI > 70 = overbought (potentially due for a pullback)
    - RSI < 30 = oversold (potentially due for a bounce)

However, for a MOMENTUM screener, we flip the typical mean-reversion
interpretation. High RSI in a trending market means strong momentum,
not necessarily "overbought." Stocks with RSI 60-80 are often in the
sweet spot of a strong uptrend.

Our scoring approach:
    - RSI 50-80 maps to higher scores (strong bullish momentum)
    - RSI > 80 gets a slight penalty (extended, higher risk)
    - RSI 20-50 maps to lower scores (weak/bearish momentum)
    - RSI < 20 is deeply bearish

Raw signal is adjusted RSI that rewards the momentum sweet spot.

Standard RSI period: 14 days
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class RSISignal(BaseSignal):

    @property
    def name(self):
        return "rsi"

    @property
    def description(self):
        return "Relative Strength Index (14-day) momentum-adjusted"

    def calculate_rsi(self, close_prices, period=14):
        """
        Calculate RSI for a series of closing prices.

        RSI = 100 - (100 / (1 + RS))
        RS = average gain over period / average loss over period
        Uses Wilder's smoothing method (exponential moving average).
        """
        deltas = close_prices.diff()

        gains = deltas.clip(lower=0)
        losses = (-deltas).clip(lower=0)

        # Wilder's smoothing: EMA with alpha = 1/period
        avg_gain = gains.ewm(alpha=1/period, min_periods=period).mean()
        avg_loss = losses.ewm(alpha=1/period, min_periods=period).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def calculate_raw(self):
        """
        For each ticker, calculate momentum-adjusted RSI.

        The raw signal transforms RSI into a momentum score:
            - RSI 50 -> 0 (neutral)
            - RSI 65 -> positive (healthy momentum)
            - RSI 80 -> strong positive (peak momentum zone)
            - RSI > 85 -> slightly reduced (overextended)
            - RSI 35 -> negative (bearish)
            - RSI 20 -> strong negative (deeply bearish)
        """
        RSI_PERIOD = 14
        MIN_HISTORY = 30  # Need enough data for RSI to stabilise

        results = []
        tickers = self.prices["ticker"].unique()

        for ticker in tickers:
            df = self.get_ticker_prices(ticker)

            if len(df) < MIN_HISTORY:
                continue

            close = df["close"].reset_index(drop=True)
            rsi_series = self.calculate_rsi(close, period=RSI_PERIOD)

            current_rsi = rsi_series.iloc[-1]

            if pd.isna(current_rsi):
                continue

            # Also get 5-day average RSI for smoothing
            avg_rsi_5d = rsi_series.tail(5).mean()

            # Transform RSI into momentum-adjusted raw signal
            # Centre at 50 (neutral), reward 60-80 zone, penalise extremes
            if current_rsi >= 50:
                # Bullish zone
                if current_rsi <= 80:
                    # Sweet spot: linear scale from 0 at RSI 50 to max at RSI 80
                    raw_signal = (current_rsi - 50) / 30
                else:
                    # Overextended: start reducing from RSI 80 peak
                    raw_signal = 1.0 - ((current_rsi - 80) / 40)
                    raw_signal = max(raw_signal, 0.2)  # Floor at 0.2
            else:
                # Bearish zone: linear scale from 0 at RSI 50 to -1 at RSI 20
                raw_signal = (current_rsi - 50) / 30
                raw_signal = max(raw_signal, -1.0)  # Floor at -1

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "rsi_14": round(current_rsi, 1),
                "rsi_5d_avg": round(avg_rsi_5d, 1),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = RSISignal(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest bullish RSI momentum):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print("\nBottom 10 (strongest bearish RSI):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
