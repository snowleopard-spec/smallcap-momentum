"""
Refresh Pipeline

Usage:
    python refresh.py           # Smart refresh (only stale data)
    python refresh.py --force   # Refresh everything regardless of age
    python refresh.py --status  # Just show what's stale, don't fetch
    python refresh.py --yes     # Skip confirmation prompts (for API use)
"""

import os
import sys
import time
import subprocess
from datetime import datetime

# Always use the same Python interpreter that launched this script.
# This means venv Python when run via cron or directly, and local
# Python when run on your Mac — no hardcoded paths needed.
PYTHON = sys.executable

DATA_SOURCES = {
    "universe":     { "file": "data/universe.parquet",       "max_age_days": 7  },
    "prices":       { "file": "data/prices_combined.parquet","max_age_days": 1  },
    "fundamentals": { "file": "data/fundamentals.parquet",   "max_age_days": 30 },
    "news":         { "file": "data/news_attention.parquet", "max_age_days": 1  },
    "insider":      { "file": "data/insider_activity.parquet","max_age_days": 14 },
}

def get_file_age_days(filepath):
    if not os.path.exists(filepath):
        return None
    return round((time.time() - os.path.getmtime(filepath)) / 86400, 1)

def is_stale(source_name):
    config = DATA_SOURCES[source_name]
    age = get_file_age_days(config["file"])
    if age is None: return True
    return age > config["max_age_days"]

def print_status():
    print(f"{'Source':<16} {'File':<35} {'Age':>8} {'Max':>6} {'Status'}")
    print("-" * 85)
    any_stale = False
    for name, config in DATA_SOURCES.items():
        age = get_file_age_days(config["file"])
        max_age = config["max_age_days"]
        if age is None:
            status, age_str, any_stale = "MISSING", "N/A", True
        elif age > max_age:
            status, age_str, any_stale = "STALE", f"{age:.1f}d", True
        else:
            status, age_str = "OK", f"{age:.1f}d"
        print(f"{name:<16} {config['file']:<35} {age_str:>8} {max_age:>5}d {status}")
    wl_age = get_file_age_days("data/watchlist.parquet")
    if wl_age is not None:
        print(f"{'watchlist':<16} {'data/watchlist.parquet':<35} {wl_age:.1f}d{'':>3} {'—':>6} output")
    return any_stale

def run_command(description, command):
    print(f"\n{'='*60}\n  {description}\n{'='*60}\n")
    start = time.time()
    result = subprocess.run(command, shell=True)
    elapsed = time.time() - start
    print(f"\n  {'✓' if result.returncode == 0 else '✗'} {'Completed' if result.returncode == 0 else 'Failed'} in {elapsed:.0f} seconds")
    return result.returncode == 0

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Refresh all data and signals")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--skip-insider", action="store_true")
    parser.add_argument("--skip-fundamentals", action="store_true")
    parser.add_argument("--signals-only", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = parser.parse_args()

    print(f"=== Refresh Pipeline ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Python: {PYTHON}")
    print(f"Mode: {'FORCE' if args.force else 'SMART'}\n")
    print("--- Current data status ---\n")
    any_stale = print_status()

    if args.status:
        print("\nSome data is stale." if any_stale else "\nAll data is up to date.")
        return

    if not args.signals_only and not any_stale and not args.force:
        print("\nAll data is up to date!")
        if not args.yes:
            response = input("Re-run signals anyway? (y/n): ").strip().lower()
            if response != "y": return

    start_time = time.time()

    if not args.signals_only:
        print("\n\n--- Step 1/5: Universe ---")
        if not args.force and not is_stale("universe"): print("  Up to date, skipping")
        else: run_command("Refreshing universe", f"{PYTHON} src/data/universe.py --refresh")

        print("\n--- Step 2/5: Prices ---")
        if os.path.exists("data/prices_combined.parquet"): run_command("Refreshing prices", f"{PYTHON} src/data/fetch_prices.py --refresh")
        else: run_command("Fetching full price history", f"{PYTHON} src/data/fetch_prices.py")

        print("\n--- Step 3/5: News ---")
        run_command("Refreshing news", f"{PYTHON} src/data/fetch_news.py")

        print("\n--- Step 4/5: Fundamentals ---")
        if args.skip_fundamentals: print("  Skipped")
        elif not args.force and not is_stale("fundamentals"): print("  Up to date, skipping")
        else: run_command("Refreshing fundamentals", f"{PYTHON} src/data/fetch_fundamentals.py --refresh")

        print("\n--- Step 5/5: Insider Activity ---")
        if args.skip_insider: print("  Skipped")
        elif not args.force and not is_stale("insider"): print("  Up to date, skipping")
        else: run_command("Refreshing insider data", f"{PYTHON} src/data/fetch_insider.py --refresh")

    print("\n--- Running Signals ---")
    run_command("Running all signals", f"{PYTHON} -m src.signals.runner --save")

    total_time = time.time() - start_time
    print(f"\n{'='*60}\n  Refresh complete in {total_time/60:.1f} minutes\n{'='*60}")
    print(f"\n--- Updated data status ---\n")
    print_status()

if __name__ == "__main__":
    main()
