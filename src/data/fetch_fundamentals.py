"""
SEC EDGAR Fundamentals Fetcher

Pulls financial statement data from SEC EDGAR's free XBRL API.
No API key required — just a User-Agent header identifying yourself.

SEC EDGAR uses CIK numbers (Central Index Keys) to identify companies,
so we first need to map our ticker symbols to CIK numbers.

Data source: data.sec.gov (RESTful JSON APIs)
Rate limit: SEC asks for max 10 requests per second
"""

import os
import time
import json
import asyncio
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# SEC requires a User-Agent header identifying who you are
# Format: "Company/App Name email@example.com"
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "SmallCapMomentum contact@example.com")

SEC_BASE_URL = "https://data.sec.gov"
SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}

# Rate limiting: SEC allows 10 requests/sec
SEC_DELAY = 0.15

# XBRL tags we want to pull (US-GAAP taxonomy)
FINANCIAL_TAGS = {
    # Balance sheet - assets
    "AssetsCurrent": "current_assets",
    "Assets": "total_assets",
    "CashAndCashEquivalentsAtCarryingValue": "cash",

    # Balance sheet - liabilities
    "LiabilitiesCurrent": "current_liabilities",
    "Liabilities": "total_liabilities",
    "LongTermDebt": "long_term_debt",
    "LongTermDebtNoncurrent": "long_term_debt_noncurrent",

    # Balance sheet - equity
    "StockholdersEquity": "stockholders_equity",

    # Income statement
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue_alt",
    "NetIncomeLoss": "net_income",
    "OperatingIncomeLoss": "operating_income",

    # Cash flow
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
}


def load_ticker_to_cik_mapping():
    """
    Load the SEC's official ticker-to-CIK mapping.
    SEC provides this as a JSON file.
    """
    cache_path = "data/ticker_cik_map.json"

    # Use cached version if less than 7 days old
    if os.path.exists(cache_path):
        age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
        if age_days < 7:
            with open(cache_path, "r") as f:
                return json.load(f)

    print("  Fetching ticker-to-CIK mapping from SEC...")
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=SEC_HEADERS)
    data = response.json()

    # Build ticker -> CIK mapping
    # SEC returns: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    mapping = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)  # SEC CIKs are zero-padded to 10 digits
        mapping[ticker] = {
            "cik": cik,
            "name": entry["title"],
        }

    # Cache it
    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(mapping, f)

    print(f"  Loaded {len(mapping)} ticker-to-CIK mappings")
    return mapping


def fetch_company_facts(cik):
    """
    Fetch all XBRL facts for a company from SEC EDGAR.
    Returns the full companyfacts JSON response.
    """
    url = f"{SEC_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"

    try:
        response = requests.get(url, headers=SEC_HEADERS)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def extract_latest_financials(company_facts):
    """
    Extract the most recent values for our target financial tags
    from a companyfacts response.

    Returns a dict of our renamed fields with their latest values.
    """
    if not company_facts or "facts" not in company_facts:
        return {}

    facts = company_facts["facts"]
    us_gaap = facts.get("us-gaap", {})

    financials = {}

    for xbrl_tag, our_name in FINANCIAL_TAGS.items():
        if xbrl_tag not in us_gaap:
            continue

        tag_data = us_gaap[xbrl_tag]
        units = tag_data.get("units", {})

        # Financial values are typically in USD
        usd_data = units.get("USD", [])
        if not usd_data:
            continue

        # Filter to 10-K (annual) and 10-Q (quarterly) filings
        # Prefer the most recent quarterly filing
        quarterly = [f for f in usd_data if f.get("form") in ("10-Q", "10-K")]
        if not quarterly:
            continue

        # Sort by end date (most recent first)
        quarterly.sort(key=lambda x: x.get("end", ""), reverse=True)

        latest = quarterly[0]
        financials[our_name] = {
            "value": latest["val"],
            "end_date": latest.get("end", ""),
            "form": latest.get("form", ""),
            "filed": latest.get("filed", ""),
        }

    return financials


def calculate_ratios(financials):
    """
    Calculate financial ratios from extracted data.
    Returns a dict of ratio name -> value.
    """
    ratios = {}

    def get_val(key):
        if key in financials:
            return financials[key]["value"]
        return None

    # Current ratio = current assets / current liabilities
    current_assets = get_val("current_assets")
    current_liabilities = get_val("current_liabilities")
    if current_assets and current_liabilities and current_liabilities != 0:
        ratios["current_ratio"] = current_assets / current_liabilities

    # Debt to equity = total liabilities / stockholders equity
    total_liabilities = get_val("total_liabilities")
    equity = get_val("stockholders_equity")
    if total_liabilities and equity and equity != 0:
        ratios["debt_to_equity"] = total_liabilities / equity

    # Cash as percentage of total assets
    cash = get_val("cash")
    total_assets = get_val("total_assets")
    if cash and total_assets and total_assets != 0:
        ratios["cash_to_assets"] = cash / total_assets

    # Revenue (use primary or alternate tag)
    revenue = get_val("revenue") or get_val("revenue_alt")
    if revenue:
        ratios["revenue"] = revenue

    # Net income
    net_income = get_val("net_income")
    if net_income:
        ratios["net_income"] = net_income

    # Net margin = net income / revenue
    if net_income and revenue and revenue != 0:
        ratios["net_margin"] = net_income / revenue

    # Operating cash flow
    ocf = get_val("operating_cash_flow")
    if ocf:
        ratios["operating_cash_flow"] = ocf

    # Long term debt (try both tags)
    ltd = get_val("long_term_debt") or get_val("long_term_debt_noncurrent")
    if ltd:
        ratios["long_term_debt"] = ltd

    # Get the most recent filing date for staleness tracking
    dates = [v["end_date"] for v in financials.values() if "end_date" in v]
    if dates:
        ratios["latest_filing_date"] = max(dates)

    return ratios


def fetch_fundamentals_for_universe(universe_df, ticker_cik_map):
    """
    Fetch fundamentals for all tickers in the universe.
    Uses sequential requests with rate limiting (SEC limit: 10/sec).
    Saves progress incrementally.
    """
    results = []
    tickers = universe_df["ticker"].tolist()
    total = len(tickers)
    matched = 0
    fetched = 0
    errors = 0

    # Load cache
    cache_path = "data/fundamentals.parquet"
    cached_tickers = set()
    if os.path.exists(cache_path):
        cached_df = pd.read_parquet(cache_path)
        cached_tickers = set(cached_df["ticker"].tolist())
        results = cached_df.to_dict("records")
        print(f"  Loaded cache: {len(cached_tickers)} tickers")

    start_time = time.time()

    for i, ticker in enumerate(tickers):
        if ticker in cached_tickers:
            continue

        # Progress
        if i > 0 and i % 50 == 0:
            elapsed = time.time() - start_time
            rate = (fetched + errors) / elapsed if elapsed > 0 else 0
            remaining_count = total - i
            remaining_time = remaining_count / rate if rate > 0 else 0
            print(f"  Progress: {i}/{total} ({i/total*100:.0f}%) | "
                  f"{rate:.1f} tickers/sec | "
                  f"~{remaining_time/60:.1f} min remaining | "
                  f"Fetched: {fetched} | Errors: {errors}")

        # Look up CIK
        if ticker not in ticker_cik_map:
            errors += 1
            continue

        cik = ticker_cik_map[ticker]["cik"]

        # Fetch from SEC
        company_facts = fetch_company_facts(cik)
        time.sleep(SEC_DELAY)

        if company_facts is None:
            errors += 1
            continue

        # Extract financials and calculate ratios
        financials = extract_latest_financials(company_facts)
        ratios = calculate_ratios(financials)

        if ratios:
            ratios["ticker"] = ticker
            ratios["cik"] = cik
            results.append(ratios)
            fetched += 1
        else:
            errors += 1

        # Save cache every 100 tickers
        if fetched > 0 and fetched % 100 == 0:
            df = pd.DataFrame(results)
            df.to_parquet(cache_path, index=False)
            print(f"  [Cache saved: {len(df)} tickers]")

    elapsed = time.time() - start_time
    print(f"\n  Completed in {elapsed/60:.1f} minutes")
    print(f"  Fetched: {fetched}")
    print(f"  Errors/skipped: {errors}")

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch SEC fundamentals for universe")
    parser.add_argument("--test", type=int, default=None,
                        help="Only fetch first N tickers (for testing)")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore cache and re-fetch all")
    args = parser.parse_args()

    print(f"=== SEC Fundamentals Fetcher ===\n")

    # Step 1: Load universe
    print("--- Step 1: Loading universe ---\n")
    universe = pd.read_parquet("data/universe.parquet")
    print(f"  Universe: {len(universe)} tickers\n")

    if args.test:
        print(f"  TEST MODE: first {args.test} tickers\n")
        universe = universe.head(args.test)

    # Step 2: Load ticker-to-CIK mapping
    print("--- Step 2: Loading ticker-to-CIK mapping ---\n")
    ticker_cik_map = load_ticker_to_cik_mapping()

    # Check coverage
    matched = sum(1 for t in universe["ticker"] if t in ticker_cik_map)
    print(f"  Matched {matched}/{len(universe)} tickers to CIK numbers\n")

    # Step 3: Clear cache if refreshing
    if args.refresh:
        cache_path = "data/fundamentals.parquet"
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print("  Cleared cache\n")

    # Step 4: Fetch fundamentals
    print(f"--- Step 3: Fetching fundamentals from SEC EDGAR ---\n")
    results = fetch_fundamentals_for_universe(universe, ticker_cik_map)

    # Step 5: Save
    if results:
        df = pd.DataFrame(results)
        os.makedirs("data", exist_ok=True)
        df.to_parquet("data/fundamentals.parquet", index=False)

        print(f"\n=== Summary ===")
        print(f"Total with fundamentals: {len(df)}")
        print(f"Saved to data/fundamentals.parquet")

        # Show coverage stats
        for col in ["current_ratio", "debt_to_equity", "cash_to_assets", "revenue", "net_margin"]:
            if col in df.columns:
                count = df[col].notna().sum()
                print(f"  {col}: {count} stocks ({count/len(df)*100:.0f}%)")

        # Show some sample data
        print(f"\nSample (first 5):")
        sample_cols = ["ticker", "current_ratio", "debt_to_equity", "net_margin", "latest_filing_date"]
        available = [c for c in sample_cols if c in df.columns]
        print(df[available].head().to_string(index=False))
    else:
        print("\nNo fundamentals data retrieved.")


if __name__ == "__main__":
    main()
