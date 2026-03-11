"""
Insider Activity Signal

Scores each stock based on recent insider transaction patterns
from SEC Form 4 filings.

For small caps, insider buying is one of the most powerful signals:
    - Insiders MUST buy with their own money (skin in the game)
    - They know the business better than any outside analyst
    - They wouldn't buy if they expected bad news ahead

Insider selling is weaker as a signal because insiders sell for
many non-bearish reasons (diversification, taxes, personal expenses).
However, heavy selling with no buying is still informative.

Signal components:
    1. Net activity direction (60%): are insiders buying or selling?
       Heavy buying = strong bullish. Heavy selling = mildly bearish.
    2. Activity level (40%): how many Form 4 filings relative to
       the universe? More activity = stronger conviction either way.

The asymmetry is intentional:
    - Insider buying is scored strongly bullish
    - Insider selling is scored only mildly bearish
    - No activity is neutral
"""

import pandas as pd
import numpy as np
from src.signals.base import BaseSignal


class InsiderActivity(BaseSignal):

    @property
    def name(self):
        return "insider_activity"

    @property
    def description(self):
        return "Insider buying/selling activity from SEC Form 4 filings"

    def __init__(self, prices_df, universe_df, insider_df=None):
        """
        Args:
            prices_df: Price data (required by base class)
            universe_df: Universe data
            insider_df: Insider activity data. If None, loads from file.
        """
        super().__init__(prices_df, universe_df)

        if insider_df is not None:
            self.insider = insider_df.copy()
        else:
            try:
                self.insider = pd.read_parquet("data/insider_activity.parquet")
            except FileNotFoundError:
                print("Warning: insider_activity.parquet not found. Run fetch_insider.py first.")
                self.insider = pd.DataFrame()

    def calculate_raw(self):
        """
        Calculate insider activity signal for each ticker.

        Scoring logic:
            - Net buy value > 0: bullish signal, scaled by magnitude
            - Net buy value < 0 (net selling): mildly bearish
            - No Form 4 filings: neutral (0)
            - Buys with no sells: extra bullish boost
            - Cluster of filings: amplifies signal
        """
        if self.insider.empty:
            return pd.DataFrame(columns=["ticker", "raw_signal"])

        results = []

        # Universe-level stats for normalisation
        all_buy_values = self.insider[self.insider["buy_value"] > 0]["buy_value"]
        median_buy_value = all_buy_values.median() if len(all_buy_values) > 0 else 1

        all_sell_values = self.insider[self.insider["sell_value"] > 0]["sell_value"]
        median_sell_value = all_sell_values.median() if len(all_sell_values) > 0 else 1

        for _, row in self.insider.iterrows():
            ticker = row["ticker"]
            filings = row["form4_filings_90d"]
            buys = row["insider_buys"]
            sells = row["insider_sells"]
            buy_value = row["buy_value"]
            sell_value = row["sell_value"]
            net_value = row["net_buy_value"]

            # No filings = neutral
            if filings == 0:
                results.append({
                    "ticker": ticker,
                    "raw_signal": 0.0,
                    "form4_count": 0,
                    "buys": 0,
                    "sells": 0,
                    "net_value": 0,
                })
                continue

            # Component 1: Net direction (60%)
            if net_value > 0:
                # Net buying — strongly bullish
                # Scale by magnitude relative to universe median
                if median_buy_value > 0:
                    magnitude = min(buy_value / median_buy_value, 5.0) / 5.0
                else:
                    magnitude = 0.5
                direction_score = magnitude

                # Bonus for pure buying (no sells at all)
                if sells == 0 and buys > 0:
                    direction_score = min(direction_score * 1.3, 1.0)

            elif net_value < 0:
                # Net selling — mildly bearish (asymmetric)
                if median_sell_value > 0:
                    magnitude = min(sell_value / median_sell_value, 5.0) / 5.0
                else:
                    magnitude = 0.5
                # Selling is only half as bearish as buying is bullish
                direction_score = -magnitude * 0.5

            else:
                # Net zero (equal buys and sells)
                direction_score = 0.0

            # Component 2: Activity level (40%)
            # More filings = more conviction
            # Cap at 10 filings for normalisation
            activity_score = min(filings / 10.0, 1.0)

            # If net selling, activity amplifies the bearish signal
            # If net buying, activity amplifies the bullish signal
            if direction_score >= 0:
                raw_signal = 0.6 * direction_score + 0.4 * activity_score * direction_score
            else:
                raw_signal = 0.6 * direction_score + 0.4 * activity_score * direction_score

            # Clamp to [-1, 1]
            raw_signal = np.clip(raw_signal, -1.0, 1.0)

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "form4_count": filings,
                "buys": buys,
                "sells": sells,
                "net_value": net_value,
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = InsiderActivity(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest insider buying):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print("\nBottom 10 (strongest insider selling):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    # Activity distribution
    insider = pd.read_parquet("data/insider_activity.parquet")
    print(f"\nActivity distribution:")
    print(f"  No Form 4 filings:    {len(insider[insider['form4_filings_90d'] == 0])} stocks")
    print(f"  With insider buys:    {len(insider[insider['insider_buys'] > 0])} stocks")
    print(f"  With insider sells:   {len(insider[insider['insider_sells'] > 0])} stocks")
    print(f"  Net buyers:           {len(insider[insider['net_buy_value'] > 0])} stocks")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
