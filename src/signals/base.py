"""
Base class for all momentum signals.

Every signal must:
1. Accept a combined price DataFrame and a universe DataFrame
2. Calculate a raw signal value for each ticker
3. Convert raw values to percentile scores (0-100)
   - 50 = neutral
   - Above 50 = bullish signal
   - Below 50 = bearish signal
4. Return a DataFrame with columns: ticker, raw_signal, score
"""

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod


class BaseSignal(ABC):
    """Base class for all signals."""

    def __init__(self, prices_df, universe_df):
        """
        Args:
            prices_df: Combined price data with columns:
                       date, ticker, open, high, low, close, volume
            universe_df: Universe data with columns:
                         ticker, name, market_cap, sic_code, primary_exchange
        """
        self.prices = prices_df.copy()
        self.universe = universe_df.copy()

        # Ensure date is datetime
        self.prices["date"] = pd.to_datetime(self.prices["date"])

    @property
    @abstractmethod
    def name(self):
        """Short name for this signal (e.g., 'price_momentum')."""
        pass

    @property
    @abstractmethod
    def description(self):
        """Human-readable description of what this signal measures."""
        pass

    @abstractmethod
    def calculate_raw(self):
        """
        Calculate the raw signal value for each ticker.

        Returns:
            DataFrame with columns: ticker, raw_signal
            Positive raw_signal = bullish
            Negative raw_signal = bearish
            Zero = neutral
        """
        pass

    def score(self):
        """
        Calculate percentile scores from raw signal values.
        Scores range from 0-100:
            - 0 = strongest bearish signal in universe
            - 50 = neutral
            - 100 = strongest bullish signal in universe

        Positive and negative raw signals are scored separately
        to ensure the 50 midpoint represents neutrality.
        """
        raw = self.calculate_raw()

        if raw.empty:
            return pd.DataFrame(columns=["ticker", "raw_signal", "score"])

        # Separate positive, negative, and zero signals
        positive = raw[raw["raw_signal"] > 0].copy()
        negative = raw[raw["raw_signal"] < 0].copy()
        zero = raw[raw["raw_signal"] == 0].copy()

        # Score positive signals: 50-100 range
        if len(positive) > 0:
            if len(positive) == 1:
                positive["score"] = 75.0
            else:
                ranks = positive["raw_signal"].rank(pct=True)
                positive["score"] = 50 + (ranks * 50)

        # Score negative signals: 0-50 range
        if len(negative) > 0:
            if len(negative) == 1:
                negative["score"] = 25.0
            else:
                # rank so that most negative = lowest score
                ranks = negative["raw_signal"].rank(pct=True)
                negative["score"] = ranks * 50

        # Zero signals get exactly 50
        if len(zero) > 0:
            zero["score"] = 50.0

        # Combine and round
        result = pd.concat([positive, negative, zero], ignore_index=True)
        result["score"] = result["score"].round(1)
        result["signal_name"] = self.name

        return result[["ticker", "signal_name", "raw_signal", "score"]]

    def get_latest_date(self):
        """Get the most recent date in the price data."""
        return self.prices["date"].max()

    def get_ticker_prices(self, ticker):
        """Get price history for a single ticker, sorted by date."""
        df = self.prices[self.prices["ticker"] == ticker].sort_values("date")
        return df

    def get_market_cap(self, ticker):
        """Get market cap for a ticker from the universe data."""
        match = self.universe[self.universe["ticker"] == ticker]
        if len(match) == 0:
            return None
        return match.iloc[0]["market_cap"]
