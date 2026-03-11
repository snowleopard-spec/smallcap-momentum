"""
Refresh Pipeline

Single command to bring all data up to date.
Checks the age of each data source and only fetches what's stale.

Usage:
    python refresh.py           # Smart refresh (only stale data)
    python refresh.py --force   # Refresh everything regardless of age
    python refresh.py --status  # Just show what's stale, don't fetch

Refresh rules:
    - Prices:       refresh if older than 1 day (trading day)
    - News:         refresh always (looks at rolling 30-day window)
    - Universe:     refresh if older than 7 days
    - Fundamentals: refresh if older than 30 days
    - Insider:      refresh if older than 14 days
    - Signals:      always re-run after any data refresh
"""

import os
import sys
import time
import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


# Data file paths and their staleness thresholds (in days)
DATA_SOURCES = {
    "universe": {
        "file": "data/universe.parquet",
        "max_age_days": 7,
        "description": "Stock universe (market caps)",
    },
    "prices": {
        "file": "data/prices_combined.parquet",
        "max_age_days": 1,
        "description": "Daily price history (OHLCV)",
    },
    "fundamentals": {
        "file": "data/fundamentals.parquet",
        "max_age_days": 30,
        "description": "SEC financial statements",
    },
    "news": {
        "file": "data/news_attention.parquet",
        "max_age_days": 1,
        "description": "News article counts",
    },
    "insider": {
        "file": "data/insider_activity.parquet",
        "max_age_days": 14,
        "description": "Insider buying/selling",
    },
}


def get_file_age_days(filepath):
    """Get the age of a file in days. Returns None if file doesn't exist."""
    if not os.path.exists(filepath):
        return None
    mtime = os.path.getmtime(filepath)
    age = (time.time() - mtime) / 86400
    return round(age, 1)


def is_stale(source_name):
    """Check if a data source needs refreshing."""
    config = DATA_SOURCES[source_name]
    age = get_file_age_days(config["file"])

    if age is None:
        return True  # File doesn't exist
    return age > config["max_age_days"]


def print_status():
    """Print the staleness status of all data sources."""
    print(f"{'Source':<16} {'File':<35} {'Age':>8} {'Max':>6} {'Status'}")
    print("-" * 85)

    any_stale = False
    for name, config in DATA_SOURCES.items():
        age = get_file_age_days(config["file"])
        max_age = config["max_age_days"]

        if age is None:
            status = "MISSING"
            age_str = "N/A"
            any_stale = True
        elif age > max_age:
            status = "STALE"
            age_str = f"{age:.1f}d"
            any_stale = True
        else:
            status = "OK"
            age_str = f"{age:.1f}d"

        print(f"{name:<16} {config['file']:<35} {age_str:>8} {max_age:>5}d {status}")

    # Also check watchlist
    watchlist_age = get_file_age_days("data/watchlist.parquet")
    if watchlist_age is None:
        print(f"{'watchlist':<16} {'data/watchlist.parquet':<35} {'N/A':>8} {'—':>6} MISSING")
    else:
        print(f"{'watchlist':<16} {'data/watchlist.parquet':<35} {watchlist_age:.1f}d:>8 {'—':>6} {'output'}")

    return any_stale


def run_command(description, command):
    """Run a shell command and report success/failure."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}\n")

    start = time.time()
    result = subprocess.run(
        command,
        shell=True,
        capture_output=False,
    )
    elapsed = time.time() - start

    if result.returncode == 0:
        print(f"\n  ✓ Completed in {elapsed:.0f} seconds")
    else:
        print(f"\n  ✗ Failed (exit code {result.returncode})")

    return result.returncode == 0


def refresh_universe(force=False):
    """Refresh the stock universe (market caps)."""
    if not force and not is_stale("universe"):
        print("  Universe: up to date, skipping")
        return True

    return run_command(
        "Refreshing universe (market caps)",
        "python src/data/universe.py --refresh"
    )


def refresh_prices(force=False):
    """Refresh price data with incremental update."""
    if not force and not is_stale("prices"):
        print("  Prices: up to date, skipping")
        return True

    # Check if we have any existing price data
    if os.path.exists("data/prices_combined.parquet"):
        # We have existing data — fetch fresh for all tickers
        # The fetcher's cache will handle individual files
        # But we want to re-fetch to get the latest days
        return run_command(
            "Refreshing price data",
            "python src/data/fetch_prices.py --refresh"
        )
    else:
        # No existing data — full fetch
        return run_command(
            "Fetching full price history",
            "python src/data/fetch_prices.py"
        )


def refresh_fundamentals(force=False):
    """Refresh SEC fundamental data."""
    if not force and not is_stale("fundamentals"):
        print("  Fundamentals: up to date, skipping")
        return True

    return run_command(
        "Refreshing SEC fundamentals",
        "python src/data/fetch_fundamentals.py --refresh"
    )


def refresh_news(force=False):
    """Refresh news attention data. Always refresh since it's a rolling window."""
    # News always refreshes because it looks at a rolling 30-day window
    return run_command(
        "Refreshing news attention data",
        "python src/data/fetch_news.py"
    )


def refresh_insider(force=False):
    """Refresh insider activity data."""
    if not force and not is_stale("insider"):
        print("  Insider activity: up to date, skipping")
        return True

    return run_command(
        "Refreshing insider activity data",
        "python src/data/fetch_insider.py --refresh"
    )


def run_signals():
    """Run all signals and generate watchlist."""
    return run_command(
        "Running all signals and generating watchlist",
        "python -m src.signals.runner --save"
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Refresh all data and signals")
    parser.add_argument("--force", action="store_true",
                        help="Refresh everything regardless of age")
    parser.add_argument("--status", action="store_true",
                        help="Just show status, don't refresh")
    parser.add_argument("--skip-insider", action="store_true",
                        help="Skip insider data (slowest to fetch)")
    parser.add_argument("--skip-fundamentals", action="store_true",
                        help="Skip fundamentals data")
    parser.add_argument("--signals-only", action="store_true",
                        help="Skip all data fetching, just re-run signals")
    args = parser.parse_args()

    print(f"=== Refresh Pipeline ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Mode: {'FORCE' if args.force else 'SMART'}\n")

    # Show current status
    print("--- Current data status ---\n")
    any_stale = print_status()

    if args.status:
        if any_stale:
            print("\nSome data is stale. Run 'python refresh.py' to update.")
        else:
            print("\nAll data is up to date.")
        return

    if not args.signals_only:
        if not any_stale and not args.force:
            print("\nAll data is up to date!")
            response = input("Re-run signals anyway? (y/n): ").strip().lower()
            if response != "y":
                return

    start_time = time.time()
    data_refreshed = False

    if not args.signals_only:
        # Step 1: Universe
        print("\n\n--- Step 1/5: Universe ---")
        if refresh_universe(args.force):
            data_refreshed = True

        # Step 2: Prices
        print("\n--- Step 2/5: Prices ---")
        if refresh_prices(args.force):
            data_refreshed = True

        # Step 3: News (always refresh)
        print("\n--- Step 3/5: News ---")
        if refresh_news(args.force):
            data_refreshed = True

        # Step 4: Fundamentals
        print("\n--- Step 4/5: Fundamentals ---")
        if args.skip_fundamentals:
            print("  Skipped (--skip-fundamentals)")
        else:
            if refresh_fundamentals(args.force):
                data_refreshed = True

        # Step 5: Insider activity
        print("\n--- Step 5/5: Insider Activity ---")
        if args.skip_insider:
            print("  Skipped (--skip-insider)")
        else:
            if refresh_insider(args.force):
                data_refreshed = True

    # Step 6: Run signals
    print("\n--- Running Signals ---")
    run_signals()

    # Summary
    total_time = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  Refresh complete in {total_time/60:.1f} minutes")
    print(f"{'='*60}")

    # Show final status
    print(f"\n--- Updated data status ---\n")
    print_status()


if __name__ == "__main__":
    main()
