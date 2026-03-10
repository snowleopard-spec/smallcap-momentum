import os
import time
import asyncio
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"

# Defaults - can be overridden via command line args
DEFAULT_MIN_MARKET_CAP = 100_000_000      # $100M
DEFAULT_MAX_MARKET_CAP = 1_000_000_000    # $1B

# Parallelism: how many requests to send at once
# Conservative start - increase if no errors
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


def load_cache():
    """Load previously fetched market cap data if it exists."""
    cache_path = "data/all_market_caps.parquet"
    if os.path.exists(cache_path):
        df = pd.read_parquet(cache_path)
        print(f"  Loaded cache: {len(df)} tickers already fetched")
        return df
    return pd.DataFrame()


def save_cache(results):
    """Save all fetched market cap data to disk."""
    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame(results)
    df.to_parquet("data/all_market_caps.parquet", index=False)


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
    Sends CONCURRENT_REQUESTS at a time, pauses briefly between batches.
    Saves progress to cache after every 500 tickers.
    """
    import aiohttp

    results = []
    total = len(tickers)
    errors = 0
    start_time = time.time()

    # Process in batches
    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, total, CONCURRENT_REQUESTS):
            batch_end = min(batch_start + CONCURRENT_REQUESTS, total)
            batch = tickers[batch_start:batch_end]

            # Create tasks for this batch
            tasks = [
                fetch_ticker_details(session, t["ticker"])
                for t in batch
            ]

            # Run batch in parallel
            batch_results = await asyncio.gather(*tasks)

            # Collect results
            for result in batch_results:
                if result is not None:
                    results.append(result)
                else:
                    errors += 1

            # Progress update every 100 tickers
            processed = batch_end
            if processed % 100 < CONCURRENT_REQUESTS:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                print(f"  Progress: {processed}/{total} ({processed/total*100:.0f}%) | "
                      f"{rate:.1f} tickers/sec | "
                      f"~{remaining/60:.1f} min remaining | "
                      f"Found: {len(results)}")

            # Save cache every 500 tickers
            if processed % 500 < CONCURRENT_REQUESTS and len(results) > 0:
                save_cache(results)
                print(f"  [Cache saved: {len(results)} tickers]")

            # Brief pause between batches
            await asyncio.sleep(BATCH_PAUSE)

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Tickers with market cap: {len(results)}")
    print(f"  Skipped/errors: {errors}")

    return results


def filter_by_market_cap(tickers_with_caps, min_cap, max_cap):
    """Filter tickers to those within the market cap range."""
    filtered = [t for t in tickers_with_caps if min_cap <= t["market_cap"] <= max_cap]
    return filtered


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build small cap universe")
    parser.add_argument("--min-cap", type=float, default=DEFAULT_MIN_MARKET_CAP,
                        help="Minimum market cap in dollars (default: 100M)")
    parser.add_argument("--max-cap", type=float, default=DEFAULT_MAX_MARKET_CAP,
                        help="Maximum market cap in dollars (default: 1B)")
    parser.add_argument("--test", type=int, default=None,
                        help="Only check first N tickers (for testing)")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore cache and re-fetch all market caps")
    parser.add_argument("--filter-only", action="store_true",
                        help="Skip fetching, just re-filter cached data")
    args = parser.parse_args()

    print(f"=== Building Universe ===")
    print(f"Market cap range: ${args.min_cap:,.0f} - ${args.max_cap:,.0f}")
    print(f"Parallel requests: {CONCURRENT_REQUESTS}\n")

    # Filter-only mode: just re-apply market cap filter to cached data
    if args.filter_only:
        print("--- Filter-only mode: using cached market cap data ---\n")
        cache = load_cache()
        if cache.empty:
            print("No cached data found. Run without --filter-only first.")
            return
        all_with_caps = cache.to_dict("records")
    else:
        # Step 1: Get all US common stock tickers
        print("--- Step 1: Fetching all US common stock tickers ---\n")
        all_tickers = get_all_us_stock_tickers()
        print(f"\nTotal US common stock tickers: {len(all_tickers)}\n")

        # Limit if testing
        if args.test:
            print(f"TEST MODE: checking first {args.test} tickers\n")
            all_tickers = all_tickers[:args.test]

        # Step 2: Check cache and skip already-fetched tickers
        if not args.refresh:
            cache = load_cache()
            if not cache.empty:
                cached_tickers = set(cache["ticker"].tolist())
                remaining = [t for t in all_tickers if t["ticker"] not in cached_tickers]
                print(f"  Already cached: {len(cached_tickers)} tickers")
                print(f"  Remaining to fetch: {len(remaining)}\n")

                if len(remaining) == 0:
                    print("  All tickers already cached! Use --refresh to re-fetch.\n")
                    all_with_caps = cache.to_dict("records")
                else:
                    # Fetch remaining tickers
                    print(f"--- Step 2: Fetching market caps for {len(remaining)} remaining tickers ---\n")
                    new_results = asyncio.run(get_market_caps_parallel(remaining))

                    # Merge with cache
                    all_with_caps = cache.to_dict("records") + new_results
            else:
                print(f"--- Step 2: Fetching market caps for {len(all_tickers)} tickers ---\n")
                all_with_caps = asyncio.run(get_market_caps_parallel(all_tickers))
        else:
            print(f"--- Step 2: Fetching market caps for {len(all_tickers)} tickers (fresh) ---\n")
            all_with_caps = asyncio.run(get_market_caps_parallel(all_tickers))

    # Step 3: Filter to target market cap range
    print(f"--- Step 3: Filtering to ${args.min_cap/1e6:.0f}M - ${args.max_cap/1e6:.0f}M ---\n")
    universe = filter_by_market_cap(all_with_caps, args.min_cap, args.max_cap)

    # Step 4: Save everything
    os.makedirs("data", exist_ok=True)

    # Save all market caps
    df_all = pd.DataFrame(all_with_caps)
    df_all.to_parquet("data/all_market_caps.parquet", index=False)
    print(f"Saved all market caps: {len(df_all)} stocks -> data/all_market_caps.parquet")

    # Save filtered universe
    df_universe = pd.DataFrame(universe)
    if len(df_universe) > 0:
        df_universe = df_universe.sort_values("market_cap", ascending=False).reset_index(drop=True)
    df_universe.to_parquet("data/universe.parquet", index=False)
    print(f"Saved universe: {len(df_universe)} stocks -> data/universe.parquet")

    # Print summary
    print(f"\n=== Summary ===")
    print(f"Total common stocks checked: {len(all_with_caps)}")
    print(f"In target range: {len(universe)}")

    if len(df_universe) > 0:
        print(f"\nMarket cap range in universe:")
        print(f"  Largest:  {df_universe.iloc[0]['ticker']} - ${df_universe.iloc[0]['market_cap']:,.0f}")
        print(f"  Smallest: {df_universe.iloc[-1]['ticker']} - ${df_universe.iloc[-1]['market_cap']:,.0f}")
        print(f"\nTop 10 by market cap:")
        for _, row in df_universe.head(10).iterrows():
            print(f"  {row['ticker']:8s} ${row['market_cap']:>15,.0f}  {row['name']}")


if __name__ == "__main__":
    main()
