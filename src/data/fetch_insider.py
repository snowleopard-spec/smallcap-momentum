"""
Insider Activity Fetcher

Pulls Form 4 insider transaction data from SEC EDGAR.
Form 4 filings are required whenever a company insider (officer, director,
or 10%+ shareholder) buys or sells company stock.

Two-step process per filing:
    1. Get filing index from www.sec.gov to find the XML filename
    2. Parse the XML for transaction details (buy/sell, shares, price)

Data source: SEC EDGAR (free, no key required)
Rate limit: SEC asks for max 10 requests per second
"""

import os
import time
import json
import re
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

SEC_DELAY = 0.15
LOOKBACK_DAYS = 90


def load_ticker_to_cik_mapping():
    """Load the cached ticker-to-CIK mapping."""
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


def get_form4_filings(cik):
    """
    Get list of recent Form 4 filings from the submissions endpoint.
    Returns list of {filing_date, accession} dicts.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    try:
        response = requests.get(url, headers=SEC_HEADERS)
        if response.status_code != 200:
            return None

        data = response.json()
        recent = data.get("filings", {}).get("recent", {})

        if not recent:
            return None

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        cutoff_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        form4s = []
        for i, form in enumerate(forms):
            if form == "4" and i < len(dates) and dates[i] >= cutoff_date:
                form4s.append({
                    "filing_date": dates[i],
                    "accession": accessions[i],
                })

        return form4s

    except Exception:
        return None


def get_form4_xml_url(cik_num, accession):
    """
    Find the XML filename for a Form 4 filing by checking the index.
    Returns the full URL to the XML file, or None.
    """
    acc_clean = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/index.json"

    try:
        response = requests.get(index_url, headers=SEC_HEADERS)
        if response.status_code != 200:
            return None

        data = response.json()
        items = data.get("directory", {}).get("item", [])

        # Find the XML file (not the index or txt file)
        for item in items:
            name = item.get("name", "")
            if name.endswith(".xml") and "index" not in name.lower():
                return f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/{name}"

        return None

    except Exception:
        return None


def parse_form4_xml(xml_text):
    """
    Parse Form 4 XML to extract non-derivative transactions.
    Returns list of transaction dicts.
    """
    transactions = []

    # Find all non-derivative transactions
    blocks = re.findall(
        r'<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>',
        xml_text, re.DOTALL
    )

    for block in blocks:
        tx = {}

        # Transaction code (P=Purchase, S=Sale, A=Award, etc.)
        code_match = re.search(r'<transactionCode>(\w)</transactionCode>', block)
        if code_match:
            tx["code"] = code_match.group(1)

        # Shares transacted
        shares_match = re.search(
            r'<transactionShares>\s*<value>([\d.]+)</value>',
            block, re.DOTALL
        )
        if shares_match:
            tx["shares"] = float(shares_match.group(1))

        # Price per share
        price_match = re.search(
            r'<transactionPricePerShare>\s*<value>([\d.]+)</value>',
            block, re.DOTALL
        )
        if price_match:
            tx["price"] = float(price_match.group(1))

        # Acquired or Disposed (A=acquired, D=disposed)
        ad_match = re.search(
            r'<acquiredDisposedCode>\s*<value>(\w)</value>',
            block, re.DOTALL
        )
        if ad_match:
            tx["acquired_disposed"] = ad_match.group(1)

        if "code" in tx:
            transactions.append(tx)

    return transactions


def fetch_insider_data_for_universe(universe_df, ticker_cik_map):
    """
    For each ticker:
        1. Get Form 4 filing list (1 API call)
        2. For tickers WITH filings, fetch up to 5 filing details
           (2 calls each: index + XML)
    """
    results = []
    tickers = universe_df["ticker"].tolist()
    total = len(tickers)
    fetched = 0
    errors = 0
    start_time = time.time()

    # Load cache
    cache_path = "data/insider_activity.parquet"
    cached_tickers = set()
    if os.path.exists(cache_path):
        cached_df = pd.read_parquet(cache_path)
        cached_tickers = set(cached_df["ticker"].tolist())
        results = cached_df.to_dict("records")
        print(f"  Loaded cache: {len(cached_tickers)} tickers")

    for i, ticker in enumerate(tickers):
        if ticker in cached_tickers:
            continue

        if i > 0 and i % 50 == 0:
            elapsed = time.time() - start_time
            processed = fetched + errors
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            print(f"  Progress: {i}/{total} ({i/total*100:.0f}%) | "
                  f"{rate:.1f} tickers/sec | "
                  f"~{remaining/60:.1f} min remaining | "
                  f"Fetched: {fetched}")

        if ticker not in ticker_cik_map:
            errors += 1
            continue

        cik = ticker_cik_map[ticker]["cik"]
        cik_num = cik.lstrip("0")  # Remove leading zeros for URL

        # Step 1: Get Form 4 filing list
        filings = get_form4_filings(cik)
        time.sleep(SEC_DELAY)

        if filings is None:
            errors += 1
            continue

        total_filings = len(filings)
        total_buys = 0
        total_sells = 0
        total_buy_value = 0
        total_sell_value = 0

        # Step 2: Parse up to 5 most recent filings for details
        for filing in filings[:5]:
            # Get XML URL from index
            xml_url = get_form4_xml_url(cik_num, filing["accession"])
            time.sleep(SEC_DELAY)

            if not xml_url:
                continue

            # Fetch and parse XML
            try:
                response = requests.get(xml_url, headers=SEC_HEADERS)
                time.sleep(SEC_DELAY)

                if response.status_code != 200:
                    continue

                transactions = parse_form4_xml(response.text)

                for tx in transactions:
                    code = tx.get("code", "")
                    shares = tx.get("shares", 0)
                    price = tx.get("price", 0)
                    value = shares * price

                    if code == "P":  # Open market purchase
                        total_buys += 1
                        total_buy_value += value
                    elif code == "S":  # Open market sale
                        total_sells += 1
                        total_sell_value += value

            except Exception:
                continue

        results.append({
            "ticker": ticker,
            "form4_filings_90d": total_filings,
            "insider_buys": total_buys,
            "insider_sells": total_sells,
            "buy_value": total_buy_value,
            "sell_value": total_sell_value,
            "net_buy_value": total_buy_value - total_sell_value,
        })
        fetched += 1

        # Save cache every 50
        if fetched > 0 and fetched % 50 == 0:
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

    parser = argparse.ArgumentParser(description="Fetch insider activity data")
    parser.add_argument("--test", type=int, default=None,
                        help="Only fetch first N tickers")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore cache and re-fetch all")
    args = parser.parse_args()

    print(f"=== Insider Activity Fetcher ===")
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
    print(f"  Matched {matched}/{len(universe)} tickers\n")

    # Clear cache if refreshing
    if args.refresh:
        cache_path = "data/insider_activity.parquet"
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print("  Cleared cache\n")

    # Fetch data
    print(f"--- Step 3: Fetching insider activity from SEC EDGAR ---\n")
    results = fetch_insider_data_for_universe(universe, ticker_cik_map)

    # Save
    if results:
        df = pd.DataFrame(results)
        os.makedirs("data", exist_ok=True)
        df.to_parquet("data/insider_activity.parquet", index=False)

        print(f"\n=== Summary ===")
        print(f"Total tickers: {len(df)}")
        print(f"Saved to data/insider_activity.parquet")

        with_filings = df[df["form4_filings_90d"] > 0]
        with_buys = df[df["insider_buys"] > 0]
        with_sells = df[df["insider_sells"] > 0]

        print(f"\nInsider activity stats (last {LOOKBACK_DAYS} days):")
        print(f"  Tickers with Form 4 filings: {len(with_filings)}")
        print(f"  Tickers with insider buys:   {len(with_buys)}")
        print(f"  Tickers with insider sells:  {len(with_sells)}")

        if len(with_buys) > 0:
            print(f"\nTop 10 by insider buy value:")
            top_buys = df.nlargest(10, "buy_value")
            for _, row in top_buys.iterrows():
                if row["buy_value"] > 0:
                    print(f"  {row['ticker']:8s} Buys: {row['insider_buys']:3d}  "
                          f"Value: ${row['buy_value']:>12,.0f}  "
                          f"Net: ${row['net_buy_value']:>12,.0f}")
    else:
        print("\nNo data retrieved.")


if __name__ == "__main__":
    main()
