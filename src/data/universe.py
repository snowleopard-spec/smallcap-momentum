import os
import json
import time
import asyncio
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"

# ── Load config ──────────────────────────────────────────────────────────────
def load_config():
    """Load market cap bounds from config.json (project root)."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
    config_path = os.path.normpath(config_path)
    if not os.path.exists(config_path):
        # Fallback: look in cwd
        config_path = "config.json"
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg["universe"]["min_market_cap"], cfg["universe"]["max_market_cap"]
    # Hard fallback if config.json is missing
    print("  Warning: config.json not found, using defaults ($500M–$2B)")
    return 500_000_000, 2_000_000_000

DEFAULT_MIN_MARKET_CAP, DEFAULT_MAX_MARKET_CAP = load_config()

# Parallelism: how many requests to send at once
CONCURRENT_REQUESTS = 10
BATCH_PAUSE = 0.5  # seconds to pause between batches


def get_all_us_stock_tickers():
    """
    Fetch all active US common stock tickers from Polygon.
    Filters to type 'CS' (Common Stock) to exclude ETFs, warrants,
    preferred shares, ADRs, etc.
    """
    tickers = []
    url = f"{BASE_URL}/v3/reference/tickers"
    params = {
        "market": "stocks",
        "active": "true",
        "locale": "us",
        "type": "CS",
        "limit": 1000,
        "apiKey": API_KEY,
    }

    page = 1
    while True:
        print(f"Fetching tickers page {page}...")
        response = requests.get(url, params=params)
        data = response.json()

        if "results" not in data:
            print(f"  Warning: {data.get('error', 'Unknown error')}")
            break

        tickers.extend(data["results"])
        print(f"  Got {len(data['results'])} tickers (total: {len(tickers)})")

        next_url = data.get("next_url")
        if not next_url:
            break

        url = next_url
        params = {"apiKey": API_KEY}
        page += 1
        time.sleep(0.2)

    return tickers


async def fetch_ticker_details(session, symbol):
    """Fetch market cap details for a single ticker."""
    url = f"{BASE_URL}/v3/reference/tickers/{symbol}"
    params = {"apiKey": API_KEY}

    try:
        async with session.get(url, params=params) as response:
            data = await response.json()

            if "results" not in data:
                return None

            details = data["results"]
            market_cap = details.get("market_cap")

            if market_cap is None:
                return None

            return {
                "ticker": symbol,
                "name": details.get("name", ""),
                "market_cap": market_cap,
                "sic_code": details.get("sic_code", ""),
                "primary_exchange": details.get("primary_exchange", ""),
            }

    except Exception:
        return None


async def get_market_caps_parallel(tickers):
    """
    Fetch market caps for all tickers using parallel requests.
    Always fetches fresh — no cache reuse — to ensure bounds are applied
    against current market caps, not stale data.
    """
    import aiohttp

    results = []
    total = len(tickers)
    errors = 0
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, total, CONCURRENT_REQUESTS):
            batch_end = min(batch_start + CONCURRENT_REQUESTS, total)
            batch = tickers[batch_start:batch_end]

            tasks = [
                fetch_ticker_details(session, t["ticker"])
                for t in batch
            ]

            batch_results = await asyncio.gather(*tasks)

            for result in batch_results:
                if result is not None:
                    results.append(result)
                else:
                    errors += 1

            processed = batch_end
            if processed % 100 < CONCURRENT_REQUESTS:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                print(f"  Progress: {processed}/{total} ({processed/total*100:.0f}%) | "
                      f"{rate:.1f} tickers/sec | "
                      f"~{remaining/60:.1f} min remaining | "
                      f"Found: {len(results)}")

            # Save progress snapshot every 500 tickers
            if processed % 500 < CONCURRENT_REQUESTS and len(results) > 0:
                _save_all_caps(results)
                print(f"  [Snapshot saved: {len(results)} tickers]")

            await asyncio.sleep(BATCH_PAUSE)

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Tickers with market cap: {len(results)}")
    print(f"  Skipped/errors: {errors}")

    return results


def _save_all_caps(results):
    """Save all fetched market cap data to disk (intermediate snapshot)."""
    os.makedirs("data", exist_ok=True)
    pd.DataFrame(results).to_parquet("data/all_market_caps.parquet", index=False)


def filter_by_market_cap(tickers_with_caps, min_cap, max_cap):
    """Filter tickers to those within the market cap range."""
    filtered = [t for t in tickers_with_caps if min_cap <= t["market_cap"] <= max_cap]
    return filtered


def main():
    import argparse

    # Read live bounds from config (already loaded at module level)
    min_cap_default = DEFAULT_MIN_MARKET_CAP
    max_cap_default = DEFAULT_MAX_MARKET_CAP

    parser = argparse.ArgumentParser(description="Build small cap universe")
    parser.add_argument("--min-cap", type=float, default=min_cap_default,
                        help=f"Minimum market cap in dollars (default from config: {min_cap_default:,.0f})")
    parser.add_argument("--max-cap", type=float, default=max_cap_default,
                        help=f"Maximum market cap in dollars (default from config: {max_cap_default:,.0f})")
    parser.add_argument("--test", type=int, default=None,
                        help="Only check first N tickers (for testing)")
    # --refresh kept for CLI compatibility but is now always the behaviour
    parser.add_argument("--refresh", action="store_true",
                        help="(Default behaviour) Always re-fetches all market caps fresh")
    parser.add_argument("--filter-only", action="store_true",
                        help="Skip fetching, just re-filter the last saved all_market_caps.parquet")
    args = parser.parse_args()

    print(f"=== Building Universe ===")
    print(f"Market cap range: ${args.min_cap:,.0f} – ${args.max_cap:,.0f}")
    print(f"Config source:    config.json")
    print(f"Parallel requests: {CONCURRENT_REQUESTS}\n")

    # ── Filter-only mode ──────────────────────────────────────────────────────
    if args.filter_only:
        print("--- Filter-only mode: re-filtering last saved market cap snapshot ---\n")
        cache_path = "data/all_market_caps.parquet"
        if not os.path.exists(cache_path):
            print("No saved snapshot found. Run without --filter-only first.")
            return
        all_with_caps = pd.read_parquet(cache_path).to_dict("records")
        print(f"  Loaded {len(all_with_caps)} tickers from snapshot")

    # ── Full fetch (default) ──────────────────────────────────────────────────
    else:
        print("--- Step 1: Fetching all US common stock tickers ---\n")
        all_tickers = get_all_us_stock_tickers()
        print(f"\nTotal US common stock tickers: {len(all_tickers)}\n")

        if args.test:
            print(f"TEST MODE: checking first {args.test} tickers\n")
            all_tickers = all_tickers[:args.test]

        print(f"--- Step 2: Fetching market caps (fresh, all {len(all_tickers)} tickers) ---\n")
        all_with_caps = asyncio.run(get_market_caps_parallel(all_tickers))

    # ── Filter ────────────────────────────────────────────────────────────────
    print(f"--- Step 3: Filtering to ${args.min_cap/1e6:.0f}M – ${args.max_cap/1e6:.0f}M ---\n")
    universe = filter_by_market_cap(all_with_caps, args.min_cap, args.max_cap)

    # ── Save ──────────────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)

    df_all = pd.DataFrame(all_with_caps)
    df_all.to_parquet("data/all_market_caps.parquet", index=False)
    print(f"Saved all market caps: {len(df_all)} stocks -> data/all_market_caps.parquet")

    df_universe = pd.DataFrame(universe)
    if len(df_universe) > 0:
        df_universe = df_universe.sort_values("market_cap", ascending=False).reset_index(drop=True)
    df_universe.to_parquet("data/universe.parquet", index=False)
    print(f"Saved universe: {len(df_universe)} stocks -> data/universe.parquet")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n=== Summary ===")
    print(f"Total common stocks checked: {len(all_with_caps)}")
    print(f"In target range (${args.min_cap/1e6:.0f}M–${args.max_cap/1e6:.0f}M): {len(universe)}")

    below = sum(1 for t in all_with_caps if t["market_cap"] < args.min_cap)
    above = sum(1 for t in all_with_caps if t["market_cap"] > args.max_cap)
    print(f"Below minimum:  {below}")
    print(f"Above maximum:  {above}")

    if len(df_universe) > 0:
        print(f"\nMarket cap range in universe:")
        print(f"  Largest:  {df_universe.iloc[0]['ticker']} – ${df_universe.iloc[0]['market_cap']:,.0f}")
        print(f"  Smallest: {df_universe.iloc[-1]['ticker']} – ${df_universe.iloc[-1]['market_cap']:,.0f}")
        print(f"\nTop 10 by market cap:")
        for _, row in df_universe.head(10).iterrows():
            print(f"  {row['ticker']:8s} ${row['market_cap']:>15,.0f}  {row['name']}")


if __name__ == "__main__":
    main()
