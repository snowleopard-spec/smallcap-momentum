import os
import time
import asyncio
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"

# Parallelism settings
CONCURRENT_REQUESTS = 10
BATCH_PAUSE = 0.5

# How many years of history to fetch
HISTORY_YEARS = 5


def load_universe():
    """Load the filtered universe of tickers."""
    path = "data/universe.parquet"
    if not os.path.exists(path):
        print("Error: universe.parquet not found. Run universe.py first.")
        return None
    df = pd.read_parquet(path)
    print(f"Loaded universe: {len(df)} tickers")
    return df


def get_already_fetched():
    """Check which tickers already have price data saved."""
    price_dir = "data/prices"
    if not os.path.exists(price_dir):
        return set()
    fetched = set()
    for f in os.listdir(price_dir):
        if f.endswith(".parquet"):
            fetched.add(f.replace(".parquet", ""))
    return fetched


async def fetch_price_history(session, symbol, start_date, end_date):
    """
    Fetch daily OHLCV data for a single ticker from Polygon.
    Uses the Aggregates (Bars) endpoint.
    """
    url = (f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day"
           f"/{start_date}/{end_date}?adjusted=true&sort=asc"
           f"&limit=50000&apiKey={API_KEY}")

    try:
        async with session.get(url) as response:
            if response.status != 200:
                return {"ticker": symbol, "error": f"HTTP {response.status}", "data": None}

            text = await response.text()
            data = json.loads(text)

            if data.get("resultsCount", 0) == 0:
                return {"ticker": symbol, "error": "no data", "data": None}

            results = data["results"]

            # Build dataframe from results
            df = pd.DataFrame(results)
            df = df.rename(columns={
                "t": "timestamp",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
                "vw": "vwap",
                "n": "transactions",
            })

            # Convert timestamp from milliseconds to date
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
            df["ticker"] = symbol

            # Keep only the columns we need
            cols = ["date", "ticker", "open", "high", "low", "close", "volume"]
            if "vwap" in df.columns:
                cols.append("vwap")
            if "transactions" in df.columns:
                cols.append("transactions")
            df = df[cols]

            return {"ticker": symbol, "error": None, "data": df}

    except Exception as e:
        return {"ticker": symbol, "error": str(e), "data": None}


async def fetch_all_prices(tickers, start_date, end_date):
    """
    Fetch price history for all tickers using parallel requests.
    Saves each ticker's data to its own Parquet file as it completes.
    """
    import aiohttp

    total = len(tickers)
    success = 0
    errors = 0
    error_samples = []
    start_time = time.time()

    os.makedirs("data/prices", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, total, CONCURRENT_REQUESTS):
            batch_end = min(batch_start + CONCURRENT_REQUESTS, total)
            batch = tickers[batch_start:batch_end]

            # Create tasks for this batch
            tasks = [
                fetch_price_history(session, symbol, start_date, end_date)
                for symbol in batch
            ]

            # Run batch in parallel
            batch_results = await asyncio.gather(*tasks)

            # Save results
            for result in batch_results:
                if result["error"] is not None:
                    errors += 1
                    if len(error_samples) < 5:
                        error_samples.append(result)
                else:
                    # Save individual ticker file
                    df = result["data"]
                    filepath = f"data/prices/{result['ticker']}.parquet"
                    df.to_parquet(filepath, index=False)
                    success += 1

            # Progress update every 100 tickers
            processed = batch_end
            if processed % 100 < CONCURRENT_REQUESTS or processed == total:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                print(f"  Progress: {processed}/{total} ({processed/total*100:.0f}%) | "
                      f"{rate:.1f} tickers/sec | "
                      f"~{remaining/60:.1f} min remaining | "
                      f"Saved: {success} | Errors: {errors}")

            # Brief pause between batches
            await asyncio.sleep(BATCH_PAUSE)

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Successfully saved: {success}")
    print(f"  Errors: {errors}")

    if error_samples:
        print(f"\n  Sample errors:")
        for e in error_samples:
            print(f"    {e['ticker']}: {e['error']}")

    return success, errors


def build_combined_file():
    """
    Combine all individual ticker Parquet files into one master file.
    This makes it easy to load all price data at once for the signal engine.
    """
    price_dir = "data/prices"
    if not os.path.exists(price_dir):
        print("No price data found.")
        return None

    files = [f for f in os.listdir(price_dir) if f.endswith(".parquet")]
    if not files:
        print("No price files found.")
        return None

    print(f"Combining {len(files)} ticker files...")
    dfs = []
    for f in files:
        df = pd.read_parquet(os.path.join(price_dir, f))
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)

    combined.to_parquet("data/prices_combined.parquet", index=False)
    print(f"Saved combined file: {len(combined)} rows, {len(files)} tickers")
    print(f"  Date range: {combined['date'].min().date()} to {combined['date'].max().date()}")
    print(f"  File: data/prices_combined.parquet")

    return combined


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch price history for universe")
    parser.add_argument("--test", type=int, default=None,
                        help="Only fetch first N tickers (for testing)")
    parser.add_argument("--years", type=int, default=HISTORY_YEARS,
                        help=f"Years of history to fetch (default: {HISTORY_YEARS})")
    parser.add_argument("--combine-only", action="store_true",
                        help="Skip fetching, just rebuild combined file")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch all tickers, ignoring cache")
    args = parser.parse_args()

    # Date range
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y-%m-%d")

    print(f"=== Price History Fetcher ===")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Parallel requests: {CONCURRENT_REQUESTS}\n")

    if args.combine_only:
        print("--- Combine-only mode ---\n")
        build_combined_file()
        return

    # Step 1: Load universe
    print("--- Step 1: Loading universe ---\n")
    universe = load_universe()
    if universe is None:
        return

    tickers = universe["ticker"].tolist()

    # Limit if testing
    if args.test:
        print(f"TEST MODE: fetching first {args.test} tickers\n")
        tickers = tickers[:args.test]

    # Step 2: Check what's already fetched
    if not args.refresh:
        already_fetched = get_already_fetched()
        remaining = [t for t in tickers if t not in already_fetched]
        print(f"  Already fetched: {len(already_fetched)} tickers")
        print(f"  Remaining: {len(remaining)}\n")

        if len(remaining) == 0:
            print("  All tickers already fetched! Use --refresh to re-fetch.\n")
            build_combined_file()
            return

        tickers = remaining

    # Step 3: Fetch prices
    print(f"--- Step 2: Fetching prices for {len(tickers)} tickers ---\n")
    success, errors = asyncio.run(fetch_all_prices(tickers, start_date, end_date))

    # Step 4: Build combined file
    print(f"\n--- Step 3: Building combined price file ---\n")
    build_combined_file()

    print(f"\n=== Done ===")


if __name__ == "__main__":
    main()
