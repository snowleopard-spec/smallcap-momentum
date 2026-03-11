"""
Financial Health Signal

Scores each stock's financial health based on SEC EDGAR fundamentals.
This acts as a QUALITY FILTER — you don't want momentum leading you
into financially distressed companies that are about to dilute or go bust.

Combines four sub-scores:
    1. Solvency (35%): current ratio and debt-to-equity
       - Can the company pay its bills? Is it over-leveraged?
    2. Cash position (25%): cash as % of total assets
       - Does it have a war chest or is it burning through cash?
    3. Profitability (25%): net margin
       - Is the business actually making money?
    4. Filing recency (15%): how recent is the latest filing
       - Stale filings are a red flag for small caps

A stock with great momentum but terrible financials is a trap.
This signal helps you avoid those.
"""

import pandas as pd
import numpy as np
from datetime import datetime
from src.signals.base import BaseSignal


class FinancialHealth(BaseSignal):

    @property
    def name(self):
        return "financial_health"

    @property
    def description(self):
        return "Financial health score from SEC fundamentals (solvency, cash, profitability)"

    def __init__(self, prices_df, universe_df, fundamentals_df=None):
        """
        Args:
            prices_df: Price data (required by base class but not used here)
            universe_df: Universe data
            fundamentals_df: SEC fundamentals data. If None, loads from file.
        """
        super().__init__(prices_df, universe_df)

        if fundamentals_df is not None:
            self.fundamentals = fundamentals_df.copy()
        else:
            try:
                self.fundamentals = pd.read_parquet("data/fundamentals.parquet")
            except FileNotFoundError:
                print("Warning: fundamentals.parquet not found. Run fetch_fundamentals.py first.")
                self.fundamentals = pd.DataFrame()

    def score_solvency(self, current_ratio, debt_to_equity):
        """
        Score solvency from 0-1.

        Current ratio:
            > 2.0 = excellent (1.0)
            1.5 - 2.0 = good (0.75)
            1.0 - 1.5 = adequate (0.5)
            0.5 - 1.0 = concerning (0.25)
            < 0.5 = danger (0.0)

        Debt to equity:
            < 0.5 = excellent (1.0)
            0.5 - 1.0 = good (0.75)
            1.0 - 2.0 = moderate (0.5)
            2.0 - 4.0 = high (0.25)
            > 4.0 = danger (0.0)
            negative = equity negative, very bad (0.0)
        """
        # Current ratio score
        if current_ratio is None or pd.isna(current_ratio):
            cr_score = 0.5  # neutral if unknown
        elif current_ratio >= 2.0:
            cr_score = 1.0
        elif current_ratio >= 1.5:
            cr_score = 0.75
        elif current_ratio >= 1.0:
            cr_score = 0.5
        elif current_ratio >= 0.5:
            cr_score = 0.25
        else:
            cr_score = 0.0

        # Debt to equity score
        if debt_to_equity is None or pd.isna(debt_to_equity):
            de_score = 0.5
        elif debt_to_equity < 0:
            de_score = 0.0  # Negative equity
        elif debt_to_equity <= 0.5:
            de_score = 1.0
        elif debt_to_equity <= 1.0:
            de_score = 0.75
        elif debt_to_equity <= 2.0:
            de_score = 0.5
        elif debt_to_equity <= 4.0:
            de_score = 0.25
        else:
            de_score = 0.0

        # Blend: 60% current ratio, 40% debt-to-equity
        return 0.6 * cr_score + 0.4 * de_score

    def score_cash_position(self, cash_to_assets):
        """Score cash position from 0-1."""
        if cash_to_assets is None or pd.isna(cash_to_assets):
            return 0.5

        # More cash is generally better for small caps
        if cash_to_assets >= 0.3:
            return 1.0
        elif cash_to_assets >= 0.15:
            return 0.75
        elif cash_to_assets >= 0.05:
            return 0.5
        elif cash_to_assets >= 0.01:
            return 0.25
        else:
            return 0.1

    def score_profitability(self, net_margin):
        """Score profitability from 0-1."""
        if net_margin is None or pd.isna(net_margin):
            return 0.5

        if net_margin >= 0.15:
            return 1.0
        elif net_margin >= 0.05:
            return 0.8
        elif net_margin >= 0.0:
            return 0.6
        elif net_margin >= -0.1:
            return 0.3
        elif net_margin >= -0.3:
            return 0.15
        else:
            return 0.0

    def score_filing_recency(self, filing_date_str):
        """
        Score how recent the latest filing is.
        Stale filings are a red flag.
        """
        if not filing_date_str or pd.isna(filing_date_str):
            return 0.25  # Unknown = concerning

        try:
            filing_date = datetime.strptime(str(filing_date_str)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return 0.25

        days_old = (datetime.now() - filing_date).days

        if days_old <= 120:      # Within last quarter
            return 1.0
        elif days_old <= 200:    # Within ~2 quarters
            return 0.75
        elif days_old <= 400:    # Within last year
            return 0.5
        elif days_old <= 600:    # Within 1.5 years
            return 0.25
        else:
            return 0.1           # Very stale

    def calculate_raw(self):
        """
        Calculate financial health score for each ticker.

        Combines:
            - Solvency: 35%
            - Cash position: 25%
            - Profitability: 25%
            - Filing recency: 15%
        """
        if self.fundamentals.empty:
            return pd.DataFrame(columns=["ticker", "raw_signal"])

        results = []

        for _, row in self.fundamentals.iterrows():
            ticker = row.get("ticker")
            if not ticker:
                continue

            # Score each component
            solvency = self.score_solvency(
                row.get("current_ratio"),
                row.get("debt_to_equity")
            )

            cash = self.score_cash_position(
                row.get("cash_to_assets")
            )

            profitability = self.score_profitability(
                row.get("net_margin")
            )

            recency = self.score_filing_recency(
                row.get("latest_filing_date")
            )

            # Weighted composite (0 to 1 scale)
            composite = (
                0.35 * solvency +
                0.25 * cash +
                0.25 * profitability +
                0.15 * recency
            )

            # Convert to raw signal: -1 to +1 scale (centred at 0)
            # 0.5 composite = 0 raw signal (neutral)
            raw_signal = (composite - 0.5) * 2

            results.append({
                "ticker": ticker,
                "raw_signal": raw_signal,
                "solvency_score": round(solvency, 2),
                "cash_score": round(cash, 2),
                "profitability_score": round(profitability, 2),
                "recency_score": round(recency, 2),
                "current_ratio": row.get("current_ratio"),
                "debt_to_equity": row.get("debt_to_equity"),
                "net_margin": row.get("net_margin"),
            })

        return pd.DataFrame(results)


if __name__ == "__main__":
    """Quick test: run signal on local data."""
    prices = pd.read_parquet("data/prices_combined.parquet")
    universe = pd.read_parquet("data/universe.parquet")

    signal = FinancialHealth(prices, universe)
    scores = signal.score()

    print(f"=== {signal.name} ===")
    print(f"{signal.description}\n")
    print(f"Scored {len(scores)} tickers\n")

    print("Top 10 (strongest financial health):")
    top = scores.nlargest(10, "score")
    for _, row in top.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print("\nBottom 10 (weakest financial health):")
    bottom = scores.nsmallest(10, "score")
    for _, row in bottom.iterrows():
        print(f"  {row['ticker']:8s} score: {row['score']:5.1f}  raw: {row['raw_signal']:+.3f}")

    print(f"\nScore distribution:")
    print(f"  Mean:   {scores['score'].mean():.1f}")
    print(f"  Median: {scores['score'].median():.1f}")
    print(f"  Std:    {scores['score'].std():.1f}")
