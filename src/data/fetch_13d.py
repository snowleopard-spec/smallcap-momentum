"""
13D Activist Filings Fetcher

Scans SEC EDGAR for recent Schedule 13D and 13D/A filings
(activist investor declarations of 5%+ ownership with intent to
influence the company) filed against tickers in our universe.

Uses two free SEC EDGAR APIs:
    1. EFTS (Full-Text Search) to find recent SC 13D filings
    2. Submissions API to cross-reference CIK → ticker

No API key required — just a User-Agent header.

Data source: efts.sec.gov (free, public)
Rate limit: SEC asks for max 10 requests per second

Usage:
    python -m src.data.fetch_13d              # Fetch and save
    python -m src.data.fetch_13d --refresh    # Clear cache and re-fetch
    python -m src.data.fetch_13d --test 5     # Test with first 5 pages
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
    "Accept": "application/json",
}

SEC_DELAY = 0.15  # 10 req/sec limit

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions"

LOOKBACK_DAYS = 90
CACHE_FILE = "data/13d_filings.parquet"


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


def build_cik_to_ticker_map(ticker_cik_map, universe_tickers):
    """
    Build a reverse mapping: CIK (unpadded) → ticker.
    Only include tickers in our universe.
    """
    cik_to_ticker = {}
    for ticker, info in ticker_cik_map.items():
        if ticker in universe_tickers:
            # Store both padded and unpadded CIK for matching
            cik_padded = info["cik"]
            cik_unpadded = cik_padded.lstrip("0") or "0"
            cik_to_ticker[cik_padded] = ticker
            cik_to_ticker[cik_unpadded] = ticker
    return cik_to_ticker


def fetch_13d_filings_from_efts(start_date, end_date, max_pages=50):
    """
    Search SEC EDGAR EFTS for SC 13D and SC 13D/A filings
    within the date range.

    The EFTS API returns filings with metadata including:
    - file_date, form_type, display_names (company names + CIKs),
      file_num, file_description

    Returns a list of raw filing dicts.
    """
    all_filings = []

    for form_type in ["SC 13D", "SC 13D/A"]:
        print(f"  Searching for {form_type} filings...")
        page = 0
        total_found = 0

        while page < max_pages:
            params = {
                "q": "*",
                "forms": form_type,
                "dateRange": "custom",
                "startdt": start_date,
                "enddt": end_date,
                "from": page * 100,
            }

            try:
                response = requests.post(EFTS_URL, headers=SEC_HEADERS, json=params)
                time.sleep(SEC_DELAY)

                if response.status_code != 200:
                    # Try GET as fallback
                    response = requests.get(
                        EFTS_URL,
                        headers=SEC_HEADERS,
                        params=params
                    )
                    time.sleep(SEC_DELAY)

                if response.status_code != 200:
                    print(f"    Error: HTTP {response.status_code}")
                    break

                data = response.json()
                hits = data.get("hits", {}).get("hits", [])

                if not hits:
                    break

                if page == 0:
                    total_val = data.get("hits", {}).get("total", {})
                    if isinstance(total_val, dict):
                        total_found = total_val.get("value", 0)
                    else:
                        total_found = total_val
                    print(f"    Found {total_found} filings")

                for hit in hits:
                    source = hit.get("_source", {})
                    filing = {
                        "form_type": form_type,
                        "file_date": source.get("file_date", ""),
                        "display_names": source.get("display_names", []),
                        "entity_name": source.get("entity_name", ""),
                        "file_num": source.get("file_num", ""),
                        "file_description": source.get("file_description", ""),
                        "period_of_report": source.get("period_of_report", ""),
                        "biz_locations": source.get("biz_locations", []),
                        "root_forms": source.get("root_forms", []),
                    }

                    # Extract CIK(s) from display_names
                    # Format: "Company Name  (CIK 0001234567)"
                    ciks = []
                    names = []
                    for dn in source.get("display_names", []):
                        # Parse "COMPANY NAME (CIK 0001234567)" format
                        if "(" in dn and ")" in dn:
                            name_part = dn.split("(")[0].strip()
                            cik_part = dn.split("(")[-1].replace(")", "").strip()
                            cik_part = cik_part.replace("CIK ", "").strip()
                            names.append(name_part)
                            ciks.append(cik_part)
                        else:
                            names.append(dn.strip())

                    filing["ciks"] = ciks
                    filing["entity_names"] = names
                    all_filings.append(filing)

                page += 1

                if len(hits) < 100:
                    break  # No more pages

            except Exception as e:
                print(f"    Error fetching page {page}: {e}")
                break

        print(f"    Collected {len([f for f in all_filings if f['form_type'] == form_type])} {form_type} filings")

    return all_filings


def match_filings_to_universe(raw_filings, cik_to_ticker, ticker_cik_map):
    """
    Match raw EFTS filings to tickers in our universe.

    A 13D filing lists the SUBJECT company (the one being invested in).
    The subject company's CIK appears in display_names.
    We match that against our universe.
    """
    matched = []
    seen = set()  # Deduplicate by (ticker, file_date, form_type)

    for filing in raw_filings:
        # Try to match any CIK in the filing to our universe
        matched_ticker = None
        for cik in filing.get("ciks", []):
            cik_clean = cik.lstrip("0") or "0"
            if cik_clean in cik_to_ticker:
                matched_ticker = cik_to_ticker[cik_clean]
                break
            if cik in cik_to_ticker:
                matched_ticker = cik_to_ticker[cik]
                break

        if not matched_ticker:
            continue

        # Deduplicate
        dedup_key = (matched_ticker, filing["file_date"], filing["form_type"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Determine the filer (activist) vs subject (company)
        # The first display_name is usually the subject company
        # The second (if present) is usually the filer/activist
        entity_names = filing.get("entity_names", [])
        subject_name = entity_names[0] if len(entity_names) > 0 else ""
        filer_name = entity_names[1] if len(entity_names) > 1 else ""

        # If there are more entities, they might be group members
        additional_filers = entity_names[2:] if len(entity_names) > 2 else []

        matched.append({
            "ticker": matched_ticker,
            "file_date": filing["file_date"],
            "form_type": filing["form_type"],
            "subject_name": subject_name,
            "filer_name": filer_name,
            "additional_filers": ", ".join(additional_filers) if additional_filers else "",
            "file_description": filing.get("file_description", ""),
            "period_of_report": filing.get("period_of_report", ""),
        })

    return matched


def fetch_13d_for_universe(universe_df, force=False):
    """
    Main pipeline: search EFTS for recent 13D filings,
    match to universe tickers, return results.
    """
    if not force and os.path.exists(CACHE_FILE):
        age_days = (time.time() - os.path.getmtime(CACHE_FILE)) / 86400
        if age_days < 7:  # Weekly refresh cadence
            print(f"  Cache is fresh ({age_days:.1f}d old), skipping fetch")
            return pd.read_parquet(CACHE_FILE).to_dict("records")

    # Build mappings
    print("  Loading ticker-to-CIK mapping...")
    ticker_cik_map = load_ticker_to_cik_mapping()
    universe_tickers = set(universe_df["ticker"].tolist())
    cik_to_ticker = build_cik_to_ticker_map(ticker_cik_map, universe_tickers)
    print(f"  Universe: {len(universe_tickers)} tickers mapped to CIKs")

    # Date range
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    print(f"  Date range: {start_date} to {end_date}")

    # Fetch from EFTS
    print(f"\n  Fetching 13D filings from SEC EDGAR EFTS...")
    raw_filings = fetch_13d_filings_from_efts(start_date, end_date)
    print(f"\n  Total raw filings found: {len(raw_filings)}")

    # Match to universe
    print(f"  Matching to universe tickers...")
    matched = match_filings_to_universe(raw_filings, cik_to_ticker, ticker_cik_map)
    print(f"  Matched {len(matched)} filings to {len(set(m['ticker'] for m in matched))} unique tickers")

    return matched


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch 13D activist filings")
    parser.add_argument("--test", type=int, default=None,
                        help="Limit EFTS pages to fetch")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore cache and re-fetch")
    args = parser.parse_args()

    print(f"=== 13D Activist Filings Fetcher ===")
    print(f"Lookback: {LOOKBACK_DAYS} days\n")

    # Load universe
    print("--- Step 1: Loading universe ---\n")
    universe = pd.read_parquet("data/universe.parquet")
    print(f"  Universe: {len(universe)} tickers\n")

    # Clear cache if refreshing
    if args.refresh and os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        print("  Cleared cache\n")

    # Fetch
    print(f"--- Step 2: Fetching 13D filings ---\n")
    results = fetch_13d_for_universe(universe, force=args.refresh)

    # Save
    if results:
        df = pd.DataFrame(results)

        # Sort by file_date descending
        df = df.sort_values("file_date", ascending=False).reset_index(drop=True)

        os.makedirs("data", exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)

        print(f"\n=== Summary ===")
        print(f"Total filings matched to universe: {len(df)}")
        print(f"Unique tickers with 13D filings: {df['ticker'].nunique()}")
        print(f"Saved to {CACHE_FILE}")

        # Breakdown
        sc13d = df[df["form_type"] == "SC 13D"]
        sc13da = df[df["form_type"] == "SC 13D/A"]
        print(f"\n  SC 13D (initial):    {len(sc13d)}")
        print(f"  SC 13D/A (amendment): {len(sc13da)}")

        # Show recent filings
        print(f"\nMost recent filings:")
        for _, row in df.head(15).iterrows():
            filer = row["filer_name"][:30] if row["filer_name"] else "Unknown"
            print(f"  {row['file_date']}  {row['ticker']:8s}  {row['form_type']:12s}  {filer}")
    else:
        print("\nNo 13D filings found for universe tickers.")
        # Save empty parquet so the pipeline doesn't error
        df = pd.DataFrame(columns=[
            "ticker", "file_date", "form_type", "subject_name",
            "filer_name", "additional_filers", "file_description",
            "period_of_report"
        ])
        os.makedirs("data", exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)
        print(f"  Saved empty file to {CACHE_FILE}")


if __name__ == "__main__":
    main()
