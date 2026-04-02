"""
13D Activist Filings Fetcher

Scans SEC EDGAR submissions for recent Schedule 13D and 13D/A filings
(activist investor declarations of 5%+ ownership with intent to
influence the company) filed against tickers in our universe.

Uses the same SEC EDGAR submissions API as the insider fetcher:
    https://data.sec.gov/submissions/CIK{cik}.json

For each ticker in the universe, checks recent filings for form types
"SC 13D", "SC 13D/A", "SCHEDULE 13D", or "SCHEDULE 13D/A".

No API key required — just a User-Agent header.

Data source: data.sec.gov (free, public)
Rate limit: SEC asks for max 10 requests per second

Usage:
    python -m src.data.fetch_13d              # Fetch and save
    python -m src.data.fetch_13d --refresh    # Clear cache and re-fetch
    python -m src.data.fetch_13d --test 20    # Test with first 20 tickers
"""

import os
import time
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "SmallCapMomentum contact@example.com")
SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}

SEC_DELAY = 0.15  # 10 req/sec limit

LOOKBACK_DAYS = 90
CACHE_FILE = "data/13d_filings.parquet"

# Form types that indicate activist intent (13D, not passive 13G)
ACTIVIST_FORM_TYPES = {"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}


def load_ticker_to_cik_mapping():
    """Load the cached ticker-to-CIK mapping (same as other SEC modules)."""
    cache_path = "data/ticker_cik_map.json"
    if os.path.exists(cache_path):
        with open(cache_path, "r") as f:
            return json.load(f)

    print("  Fetching ticker-to-CIK mapping from SEC...")
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=SEC_HEADERS)
    data = response.json()

    mapping = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)
        mapping[ticker] = {"cik": cik, "name": entry["title"]}

    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(mapping, f)

    return mapping


def get_13d_filings_for_cik(cik, cutoff_date):
    """
    Check a company's recent submissions for SC 13D filings.

    Returns list of filing dicts, or empty list if none found.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    try:
        response = requests.get(url, headers=SEC_HEADERS)
        if response.status_code != 200:
            return []

        data = response.json()
        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        filings = []
        for i, form in enumerate(forms):
            if form not in ACTIVIST_FORM_TYPES:
                continue

            filing_date = dates[i] if i < len(dates) else ""
            if filing_date < cutoff_date:
                continue

            # Normalise form type for consistency
            if "SCHEDULE" in form:
                normalised_form = form.replace("SCHEDULE ", "SC ")
            else:
                normalised_form = form

            filings.append({
                "file_date": filing_date,
                "form_type": normalised_form,
                "accession": accessions[i] if i < len(accessions) else "",
                "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                "description": descriptions[i] if i < len(descriptions) else "",
            })

        return filings

    except Exception:
        return []


def fetch_13d_for_universe(universe_df, ticker_cik_map, force=False):
    """
    For each ticker in the universe, check SEC submissions for
    recent 13D filings. Returns list of matched filings.
    """
    if not force and os.path.exists(CACHE_FILE):
        age_days = (time.time() - os.path.getmtime(CACHE_FILE)) / 86400
        if age_days < 7:  # Weekly refresh cadence
            print(f"  Cache is fresh ({age_days:.1f}d old), skipping fetch")
            return pd.read_parquet(CACHE_FILE).to_dict("records")

    tickers = universe_df["ticker"].tolist()
    total = len(tickers)
    cutoff_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    results = []
    checked = 0
    matched_tickers = 0
    errors = 0
    start_time = time.time()

    for i, ticker in enumerate(tickers):
        if ticker not in ticker_cik_map:
            errors += 1
            continue

        cik = ticker_cik_map[ticker]["cik"]
        company_name = ticker_cik_map[ticker].get("name", "")

        filings = get_13d_filings_for_cik(cik, cutoff_date)
        time.sleep(SEC_DELAY)

        if filings:
            matched_tickers += 1
            for filing in filings:
                results.append({
                    "ticker": ticker,
                    "company_name": company_name,
                    "file_date": filing["file_date"],
                    "form_type": filing["form_type"],
                    "accession": filing["accession"],
                    "description": filing.get("description", ""),
                })

        checked += 1

        # Progress
        if checked > 0 and checked % 100 == 0:
            elapsed = time.time() - start_time
            rate = checked / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            print(f"  Progress: {i}/{total} ({i/total*100:.0f}%) | "
                  f"{rate:.1f} tickers/sec | "
                  f"~{remaining/60:.1f} min remaining | "
                  f"Found: {len(results)} filings in {matched_tickers} tickers")

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Checked: {checked} tickers")
    print(f"  Found: {len(results)} filings in {matched_tickers} tickers")
    print(f"  Errors/skipped: {errors}")

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch 13D activist filings")
    parser.add_argument("--test", type=int, default=None,
                        help="Only check first N tickers")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore cache and re-fetch")
    args = parser.parse_args()

    print(f"=== 13D Activist Filings Fetcher ===")
    print(f"Lookback: {LOOKBACK_DAYS} days\n")

    # Load universe
    print("--- Step 1: Loading universe ---\n")
    universe = pd.read_parquet("data/universe.parquet")
    print(f"  Universe: {len(universe)} tickers\n")

    if args.test:
        print(f"  TEST MODE: first {args.test} tickers\n")
        universe = universe.head(args.test)

    # Load CIK mapping
    print("--- Step 2: Loading ticker-to-CIK mapping ---\n")
    ticker_cik_map = load_ticker_to_cik_mapping()
    matched = sum(1 for t in universe["ticker"] if t in ticker_cik_map)
    print(f"  Matched {matched}/{len(universe)} tickers to CIK numbers\n")

    # Clear cache if refreshing
    if args.refresh and os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        print("  Cleared cache\n")

    # Fetch
    print(f"--- Step 3: Scanning submissions for 13D filings ---\n")
    results = fetch_13d_for_universe(universe, ticker_cik_map, force=args.refresh)

    # Save
    if results:
        df = pd.DataFrame(results)
        df = df.sort_values("file_date", ascending=False).reset_index(drop=True)

        os.makedirs("data", exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)

        print(f"\n=== Summary ===")
        print(f"Total filings: {len(df)}")
        print(f"Unique tickers: {df['ticker'].nunique()}")
        print(f"Saved to {CACHE_FILE}")

        sc13d = df[df["form_type"] == "SC 13D"]
        sc13da = df[df["form_type"] == "SC 13D/A"]
        print(f"\n  SC 13D (initial):    {len(sc13d)}")
        print(f"  SC 13D/A (amendment): {len(sc13da)}")

        print(f"\nRecent filings:")
        for _, row in df.head(15).iterrows():
            print(f"  {row['file_date']}  {row['ticker']:8s}  {row['form_type']:12s}  {row['company_name'][:35]}")
    else:
        print("\nNo 13D filings found for universe tickers.")
        df = pd.DataFrame(columns=[
            "ticker", "company_name", "file_date", "form_type",
            "accession", "description"
        ])
        os.makedirs("data", exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)
        print(f"  Saved empty file to {CACHE_FILE}")


if __name__ == "__main__":
    main()
