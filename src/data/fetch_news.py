"""
News Attention Fetcher

Pulls recent news article counts for each ticker in the universe
from Polygon's Ticker News endpoint.

Captures:
    - Total articles in the last 30 days
    - Articles in the last 7 days
    - Used to calculate attention surge and level

Polygon News endpoint: /v2/reference/news
Rate limit: unlimited on Starter plan
"""

import os
import time
import asyncio
import json
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io"

CONCURRENT_REQUESTS = 10
BATCH_PAUSE = 0.5


async def fetch_news_count(session, symbol, published_after):
    """
    Fetch news articles for a single ticker since a given date.
    Returns the count and list of article dates.
    """
    url = (f"{BASE_URL}/v2/reference/news"
           f"?ticker={symbol}"
           f"&published_utc.gte={published_after}"
           f"&limit=50"
           f"&apiKey={API_KEY}")

    try:
        async with session.get(url) as response:
            if response.status != 200:
                return {"ticker": symbol, "error": f"HTTP {response.status}"}

            text = await response.text()
            data = json.loads(text)

            results = data.get("results", [])

            # Extract article dates
            article_dates = []
            for article in results:
                pub_date = article.get("published_utc", "")
                if pub_date:
                    article_dates.append(pub_date[:10])  # YYYY-MM-DD

            return {
                "ticker": symbol,
                "articles": article_dates,
                "total_count": len(results),
                "error": None,
            }

    except Exception as e:
        return {"ticker": symbol, "error": str(e)}


async def fetch_all_news(tickers):
    """
    Fetch news counts for all tickers in parallel.
    Looks back 30 days.
    """
    import aiohttp

    published_after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    total = len(tickers)
    results = []
    errors = 0
    error_samples = []
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, total, CONCURRENT_REQUESTS):
            batch_end = min(batch_start + CONCURRENT_REQUESTS, total)
            batch = tickers[batch_start:batch_end]

            tasks = [
                fetch_news_count(session, symbol, published_after)
                for symbol in batch
            ]

            batch_results = await asyncio.gather(*tasks)

            for result in batch_results:
                if result["error"] is not None:
                    errors += 1
                    if len(error_samples) < 5:
                        error_samples.append(result)
                else:
                    # Split into 7-day and 30-day counts
                    all_dates = result["articles"]
                    count_30d = len(all_dates)
                    count_7d = sum(1 for d in all_dates if d >= seven_days_ago)

                    results.append({
                        "ticker": result["ticker"],
                        "news_count_30d": count_30d,
                        "news_count_7d": count_7d,
                    })

            # Progress
            processed = batch_end
            if processed % 100 < CONCURRENT_REQUESTS or processed == total:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                print(f"  Progress: {processed}/{total} ({processed/total*100:.0f}%) | "
                      f"{rate:.1f} tickers/sec | "
                      f"~{remaining/60:.1f} min remaining | "
                      f"Fetched: {len(results)} | Errors: {errors}")

            await asyncio.sleep(BATCH_PAUSE)

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Fetched: {len(results)}")
    print(f"  Errors: {errors}")

    if error_samples:
        print(f"\n  Sample errors:")
        for e in error_samples:
            print(f"    {e['ticker']}: {e['error']}")

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch news attention data")
    parser.add_argument("--test", type=int, default=None,
                        help="Only fetch first N tickers")
    args = parser.parse_args()

    print(f"=== News Attention Fetcher ===\n")

    # Load universe
    print("--- Step 1: Loading universe ---\n")
    universe = pd.read_parquet("data/universe.parquet")
    tickers = universe["ticker"].tolist()
    print(f"  Universe: {len(tickers)} tickers\n")

    if args.test:
        print(f"  TEST MODE: first {args.test} tickers\n")
        tickers = tickers[:args.test]

    # Fetch news
    print(f"--- Step 2: Fetching news data for {len(tickers)} tickers ---\n")
    results = asyncio.run(fetch_all_news(tickers))

    # Save
    if results:
        df = pd.DataFrame(results)
        os.makedirs("data", exist_ok=True)
        df.to_parquet("data/news_attention.parquet", index=False)

        print(f"\n=== Summary ===")
        print(f"Total tickers: {len(df)}")
        print(f"Saved to data/news_attention.parquet")
        print(f"\nNews coverage stats:")
        print(f"  Tickers with 0 articles (30d):  {len(df[df['news_count_30d'] == 0])}")
        print(f"  Tickers with 1+ articles (30d): {len(df[df['news_count_30d'] > 0])}")
        print(f"  Tickers with 5+ articles (30d): {len(df[df['news_count_30d'] >= 5])}")
        print(f"  Max articles (30d):             {df['news_count_30d'].max()}")

        print(f"\nTop 10 by news coverage (30d):")
        top = df.nlargest(10, "news_count_30d")
        for _, row in top.iterrows():
            print(f"  {row['ticker']:8s} 30d: {row['news_count_30d']:3d}  7d: {row['news_count_7d']:3d}")
    else:
        print("\nNo data retrieved.")


if __name__ == "__main__":
    main()
