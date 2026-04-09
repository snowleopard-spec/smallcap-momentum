"""Quick 13G/13D density check for your universe"""
import requests, json, time, pandas as pd

SEC_HEADERS = {
    "User-Agent": "SmallCapMomentum your@email.com",
    "Accept-Encoding": "gzip, deflate",
}

# Load your universe and CIK map
universe = pd.read_parquet("data/universe.parquet")
with open("data/ticker_cik_map.json") as f:
    cik_map = json.load(f)

tickers = universe["ticker"].tolist()[:50]  # sample 50

total_13g = 0
total_13d = 0
tickers_with = 0
recent_90d = 0

for ticker in tickers:
    cik = cik_map.get(ticker)
    if not cik:
        continue
    
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = requests.get(url, headers=SEC_HEADERS)
    time.sleep(0.15)
    
    if resp.status_code != 200:
        continue
    
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    
    hits = []
    for i, form in enumerate(forms):
        if form in ("SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"):
            hits.append((form, dates[i]))
            if dates[i] >= "2026-01-08":
                recent_90d += 1
    
    if hits:
        tickers_with += 1
        total_13g += sum(1 for f, _ in hits if "13G" in f)
        total_13d += sum(1 for f, _ in hits if "13D" in f)
        print(f"  {ticker:8s} {len(hits):3d} filings | Latest: {hits[0][0]} ({hits[0][1]})")
    else:
        print(f"  {ticker:8s}   0")

print(f"\n=== Summary ({len(tickers)} tickers) ===")
print(f"  With 13G/13D filings: {tickers_with}")
print(f"  Total 13G: {total_13g}, Total 13D: {total_13d}")
print(f"  Filed in last 90 days: {recent_90d}")