"""
News Attention Signal

Scores each stock based on recent news coverage from Polygon's
news endpoint. For small caps, media attention is a meaningful
signal — most of these stocks get zero coverage most of the time,
so any attention is notable.

Combines two components:
    1. Attention level (50%): absolute news count over 30 days,
       relative to the universe. More articles = more attention.
    2. Attention surge (50%): 7-day count vs 30-day average weekly rate.
       A spike in coverage suggests something is happening NOW.

The signal is made directional using price performance:
    - News + price up = bullish (positive signal)
    - News + price down = bearish (negative signal)
    - No news = neutral

This captures the idea that rising attention on a rising stock
is institutional interest building, while rising attention on a
falling stock might be bad press.
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class NewsAttention(BaseSignal):

    @property
    def name(self):
        return "news_attention"

    @property
    def description(self):
        return "News media attention level and surge, direction-adjusted"

    def __init__(self, prices_df, universe_df, news_df=None):
        """
        Args:
            prices_df: Price data
            universe_df: Universe data
            news_df: News attention data. If None, loads from file.
        """
        super().__init__(prices_df, universe_df)

        if news_df is not None:
            self.news = news_df.copy()
        else:
            try:
                self.news = pd.read_parquet("data/news_attention.parquet")
            except FileNotFoundError:
                print("Warning: news_attention.parquet not found. Run fetch_news.py first.")
                self.news = pd.DataFrame()

    def calculate_raw(self):
        """
        For each ticker, calculate direction-adjusted attention score.

        Components:
            1. Attention level: 30-day article count, log-scaled
               (log scale because the distribution is heavily skewed)
            2. Attention surge: 7-day count vs expected weekly rate
            3. Direction: 7-day price return sign
        """
        if self.news.empty:
            return pd.DataFrame(columns=["ticker", "raw_signal"])

        results = []

        # Calculate universe-level stats for relative scoring
        median_30d = self.news["news_count_30d"].median()
        max_30d = self.news["news_count_30d"].max()

        for _, news_row in self.news.iterrows():
            ticker = news_row["ticker"]
            count_30d = news_row["news_count_30d"]
            count_7d = news_row["news_count_7d"]

            # Component 1: Attention level (log-scaled, 0 to 1)
            if count_30d == 0:
                level_score = 0.0
            else:
                # Log scale: log(1+count) / log(1+max)
                level_score = np.log1p(count_30d) / np.log1p(max(max_30d, 1))

            # Component 2: Attention surge
            # Expected weekly rate based on 30-day count
            expected_weekly = count_30d / 4.3  # ~4.3 weeks in 30 days

            if expected_weekly > 0:
                surge_ratio = count_7d / expected_weekly
                # Normalise: 1.0 = normal, >1 = surge, <1 = declining
                surge_score = (surge_ratio - 1.0) / 2.0  # Scale so 3x = 1.0
                surge_score = np.clip(surge_score, -0.5, 1.0)
            elif count_7d > 0:
                # Had no articles before but has some now — big surge
                surge_score = 1.0
            else:
                surge_score = 0.0

            # Component 3: Price direction over last 7 days
            df = self.get_ticker_prices(ticker)
            if len(df) >= 7:
                price_recent = df.iloc[-1]["close"]
                price_7d_ago = df.iloc[-6]["close"]  # ~5 trading days

                if price_7d_ago > 0:
                    price_return = (price_recent - price_7d_ago) / price_7d_ago
                    direction = np.sign(price_return)
                    if direction == 0:
                        direction = 0.5  # Flat with attention = mildly bullish
                else:
                    direction = 0.0
                    price_return = 0.0
            else:
                direction = 0.0
                price_return = 0.0

            # Combine components
            # If no news at all, signal is neutral (0)
            if count_30d == 0 and count_7d == 0:
                raw_signal = 0.0
            else:
                # Attention magnitude (always positive)
                attention = 0.5 * level_score + 0.5 * max(surge_score, 0)
                # Apply direction
                raw_signal = attention * direction

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "news_30d": count_30d,
                "news_7d": count_7d,
                "surge_score": round(surge_score, 3),
                "price_return_7d": round(price_return, 4),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = NewsAttention(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (highest bullish attention):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    print("\nBottom 10 (highest bearish attention):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.4f}")

    # Show attention distribution
    news = pd.read_parquet("data/news_attention.parquet")
    print(f"\nAttention distribution:")
    print(f"  Zero articles (30d): {len(news[news['news_count_30d'] == 0])} stocks")
    print(f"  1-5 articles (30d):  {len(news[(news['news_count_30d'] > 0) & (news['news_count_30d'] <= 5)])} stocks")
    print(f"  5+ articles (30d):   {len(news[news['news_count_30d'] > 5])} stocks")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
