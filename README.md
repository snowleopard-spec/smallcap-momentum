# Unicorn Hunt — Small Cap Momentum Screener

**Live at [unicornpunk.org](https://unicornpunk.org)**

A quantitative small-cap stock screener that combines eight momentum and quality signals to surface US equities in the $500M–$2B market cap range showing unusual strength. The system fetches data from multiple sources (Polygon, SEC EDGAR), scores every ticker on a 0–100 percentile scale across each signal, and produces a ranked watchlist updated daily by cron.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Data Pipeline](#data-pipeline)
- [Signal Engine](#signal-engine)
- [Backend API](#backend-api)
- [Frontend](#frontend)
- [Server & Deployment](#server--deployment)
- [Cron & Automated Refresh](#cron--automated-refresh)
- [Email Monitoring](#email-monitoring)
- [Configuration](#configuration)
- [Data Files](#data-files)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────┐     ┌──────────────────┐     ┌───────────────────────┐
│   Data Sources       │     │   Backend (DO)    │     │   Frontend (CF)        │
│                      │     │                   │     │                        │
│  Polygon.io API ─────┼────▶│  refresh.py       │     │  React + Vite          │
│  SEC EDGAR XBRL ─────┼────▶│  src/data/*.py    │     │  UnicornHunt.jsx       │
│  SEC EDGAR Form 4 ───┼────▶│  src/signals/*.py │     │  Hosted on Cloudflare  │
│                      │     │  api.py (FastAPI) │◀───▶│  Pages                 │
└─────────────────────┘     │                   │     └───────────────────────┘
                             │  Cron (9pm UTC)   │
                             │  refresh_monitor  │──▶  Email via Resend
                             └──────────────────┘
```

The system has three layers:

1. **Data pipeline** — Python scripts that fetch, cache, and combine data from external APIs into Parquet files.
2. **Signal engine** — Eight signal classes that each score every ticker, plus a runner that produces a weighted composite and ranked watchlist.
3. **API + Frontend** — A FastAPI backend serving the watchlist and supporting live weight recalculation, consumed by a React single-page app with a retro DOS-terminal aesthetic.

---

## Data Pipeline

Each data source has its own fetch script in `src/data/`. They all write Parquet files to the `data/` directory and support `--refresh` (clear cache) and `--test N` (fetch only first N tickers) flags.

### Universe (`src/data/universe.py`)

Builds the investable universe by fetching all active US common stocks (type "CS") from Polygon, then filtering to the market cap range defined in `config.json` (default $500M–$2B). It stores both the full market cap snapshot (`data/all_market_caps.parquet`) and the filtered universe (`data/universe.parquet`). Market caps are always re-fetched fresh — no stale cache reuse — so the bounds are applied against current values.

- **Source:** Polygon `/v3/reference/tickers` + `/v3/reference/tickers/{symbol}`
- **Refresh cadence:** Weekly (7 days)
- **Parallelism:** 10 concurrent async requests with 0.5s batch pause

### Prices (`src/data/fetch_prices.py`)

Fetches 5 years of daily OHLCV data for every ticker in the universe. Individual ticker files are saved to `data/prices/` as they complete, then combined into a single `data/prices_combined.parquet` for the signal engine. On refresh, only missing tickers are fetched (unless `--refresh` forces a full re-fetch).

- **Source:** Polygon `/v2/aggs/ticker/{symbol}/range/1/day`
- **Refresh cadence:** Daily (1 day)
- **Output:** ~1.5M rows in the combined file covering ~1,600 tickers × 5 years

### Fundamentals (`src/data/fetch_fundamentals.py`)

Pulls financial statement data from SEC EDGAR's free XBRL API. Maps tickers to CIK numbers using SEC's official mapping file, then fetches `companyfacts` for each. Extracts balance sheet items (current assets/liabilities, cash, equity, long-term debt), income statement items (revenue, net income, operating income), and cash flow. Calculates key ratios: current ratio, debt-to-equity, cash-to-assets, net margin.

- **Source:** SEC EDGAR `data.sec.gov/api/xbrl/companyfacts`
- **Refresh cadence:** Monthly (30 days)
- **Rate limit:** 10 requests/second (SEC policy), enforced with 0.15s delay
- **Cache:** Saves progress every 100 tickers; resumes from cache on restart

### News (`src/data/fetch_news.py`)

Fetches 30-day article counts for each ticker from Polygon's news endpoint. For small caps, any media attention is meaningful, so the signal captures both 30-day level and 7-day surge.

- **Source:** Polygon `/v2/reference/news`
- **Refresh cadence:** Daily (1 day)
- **Parallelism:** 10 concurrent async requests

### Insider Activity (`src/data/fetch_insider.py`)

Pulls Form 4 insider transaction data from SEC EDGAR. For each ticker, it first fetches the filing list, then parses the XML of up to 5 most recent filings to extract open-market purchases (code "P") and sales (code "S") with share counts and dollar values.

- **Source:** SEC EDGAR submissions + Form 4 XML
- **Refresh cadence:** Fortnightly (14 days)
- **Lookback:** 90 days of filings
- **Rate limit:** Same SEC 10/sec policy

---

## Signal Engine

All signals live in `src/signals/` and inherit from `BaseSignal` (`base.py`). The base class handles:

- Filtering price data to universe tickers only
- Converting raw signals (arbitrary scale, positive = bullish) to percentile scores (0–100, 50 = neutral)
- Positive and negative raw values are scored separately to preserve the neutral midpoint

### Signals

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| **Price Momentum** | 20% | Composite 3/6/12-month returns, skipping the most recent month to avoid short-term reversal. Captures sustained trend. |
| **Volume Surge** | 20% | Recent 5-day volume vs 60-day average, normalised by market cap, direction-adjusted. Measures conviction — elevated volume on an up-move is bullish. |
| **Insider Buying** | 20% | Form 4 purchases vs sales over 90 days. Asymmetric scoring: buying is strongly bullish, selling only mildly bearish (insiders sell for many benign reasons). |
| **Financial Health** | 15% | Composite of solvency (35%), cash position (25%), profitability (25%), and filing recency (15%). Acts as a quality filter against distressed companies. |
| **Stochastic** | 10% | Slow Stochastic (14,3,3) combining level (50%), crossover (30%), and trend (20%). Where price closed relative to its range. |
| **Price Acceleration** | 10% | Rate of change of momentum. Catches stocks early in their move before they show up on simple screens. |
| **News Attention** | 5% | 30-day article count with 7-day surge detection. Direction-adjusted — positive price action with news coverage is more meaningful. |
| **RSI** | 0% | 14-day RSI tuned for momentum. Sweet spot 60–80 scores highest, overextended >85 is penalised. Currently disabled (weight 0) but available. |

### Signal Runner (`src/signals/runner.py`)

Loads all data, instantiates each signal, runs scoring, and produces the composite watchlist. The composite score is a weighted average of individual signal scores, with missing signals treated as neutral (50). Outputs `data/watchlist.parquet` with all individual signal scores and a composite rank.

Run standalone: `python -m src.signals.runner --save`

---

## Backend API

**File:** `api.py`  
**Framework:** FastAPI with Uvicorn  
**Port:** 8000

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Data freshness for each source (age, staleness, universe count) |
| `GET` | `/api/config` | Universe bounds and signal weights from `config.json` |
| `GET` | `/api/watchlist` | Full ranked watchlist with all scores, fundamentals, insider data, and price info |
| `GET` | `/api/prices/{ticker}` | Daily close prices for a single ticker (default 365 days) |
| `POST` | `/api/recalc` | Re-rank watchlist with custom weights (body: `{"weights": {...}}`) — instant, no refetch |
| `POST` | `/api/refresh` | Trigger a background smart refresh (skips fresh data) |
| `POST` | `/api/reset` | Trigger a background force refresh (re-fetches everything) |
| `GET` | `/api/progress` | Poll refresh progress (step, percentage, detail) |

CORS is configured for `localhost:3000`, `localhost:5173`, `unicornpunk.org`, and `smallcap-momentum.pages.dev`.

### Running

```bash
uvicorn api:app --reload --port 8000        # development
uvicorn api:app --host 0.0.0.0 --port 8000  # production
```

---

## Frontend

**Directory:** `frontend/`  
**Stack:** React 19 + Vite 7  
**Hosting:** Cloudflare Pages (built from the `frontend/` directory)  
**Style:** Retro DOS-terminal aesthetic with neon orange/green on dark backgrounds

### Key Components

- **HeroBanner** — Synthwave-styled header with perspective grid
- **ControlPanel** — Displays market cap range (read-only, set via `config.json`) and universe count, with refresh/recalc buttons
- **WeightsPanel** — Interactive sliders for each signal weight. Adjusting one automatically rebalances others to maintain 100% total. Changes take effect on next recalc.
- **DOSTerminal** — The main output: top-20 ranked stocks in a monospaced terminal format. Click any row to expand a detail panel showing fundamentals, insider activity, and price chart.
- **ChartsGrid** — Sparkline price charts for each watchlist stock

### Build & Deploy

```bash
cd frontend
npm install
npm run dev          # local dev server at localhost:5173
npm run build        # production build to frontend/dist/
```

The `VITE_API_URL` environment variable points to the backend. Set it in Cloudflare Pages environment settings for production (e.g., `https://unicornpunk.org:8000` or wherever the API is exposed).

---

## Server & Deployment

### Infrastructure

- **Server:** DigitalOcean Droplet (Linux/Ubuntu)
- **Hostname:** `unicorn-hunt` (internal)
- **Domain:** `unicornpunk.org`
- **Frontend hosting:** Cloudflare Pages (separate from the droplet)

### Server Setup

The backend runs inside a Python virtual environment at `/home/smallcap-momentum/`:

```
/home/smallcap-momentum/
├── api.py                  # FastAPI backend
├── refresh.py              # Data refresh pipeline
├── refresh_monitor.py      # Monitoring wrapper (sends email reports)
├── config.json             # Universe bounds + signal weights
├── .env                    # API keys (not in git)
├── requirements.txt
├── venv/                   # Python 3.12 virtual environment
├── data/                   # All Parquet data files (not in git)
│   ├── universe.parquet
│   ├── prices_combined.parquet
│   ├── prices/             # Individual ticker price files
│   ├── fundamentals.parquet
│   ├── news_attention.parquet
│   ├── insider_activity.parquet
│   ├── watchlist.parquet
│   ├── all_market_caps.parquet
│   └── ticker_cik_map.json
├── src/
│   ├── data/               # Data fetch modules
│   └── signals/            # Signal scoring modules
├── frontend/               # React app (deployed separately to Cloudflare)
└── refresh.log             # Cron output log
```

### Disk Management

The `data/` directory holds all Parquet files. Key sizes to be aware of:

- `prices_combined.parquet` is the largest file (~1.5M rows across ~1,600 tickers × 5 years of daily data)
- `data/prices/` contains individual ticker files used during the fetch process and for building the combined file
- `all_market_caps.parquet` is a snapshot of every US common stock's market cap (used for universe filtering)

The data directory is excluded from git. If you need to rebuild from scratch, run `python refresh.py --force` which will re-fetch everything.

### Process Management

The FastAPI server should be kept running persistently. Options include `systemd`, `screen`, or `tmux`:

```bash
# Using screen
screen -S api
cd /home/smallcap-momentum
source venv/bin/activate
uvicorn api:app --host 0.0.0.0 --port 8000
# Ctrl+A, D to detach
```

---

## Cron & Automated Refresh

A single cron job runs the full pipeline daily at 9pm UTC (5am SGT), after US market close:

```
0 21 * * * bash -c "cd /home/smallcap-momentum && source /home/smallcap-momentum/venv/bin/activate && python refresh_monitor.py --yes" >> /home/smallcap-momentum/refresh.log 2>&1
```

### What the cron does

1. `refresh_monitor.py` wraps `refresh.py` — it snapshots all data file timestamps before the run, then invokes the pipeline.
2. `refresh.py --yes` runs a **smart refresh**: it checks each data source's age against its staleness threshold and only re-fetches what's actually stale. On a typical day, this means:
   - **Always runs:** News (daily), Signals (every run)
   - **Runs if stale:** Prices (daily), Insider (14d), Universe (7d), Fundamentals (30d)
3. After the pipeline completes, `refresh_monitor.py` compares file timestamps to determine what was actually updated, captures peak memory usage and errors, and sends an email report via Resend.

### Typical daily run

On a day where only news and signals need updating (prices are fresh from the previous run), the pipeline takes approximately 3–4 minutes.

### Editing the cron

```bash
crontab -e          # edit
crontab -l          # verify
```

### Checking the log

```bash
tail -100 /home/smallcap-momentum/refresh.log
```

### Reverting to the unwrapped pipeline

If the monitor causes issues, switch the cron back to calling `refresh.py` directly:

```
0 21 * * * bash -c "cd /home/smallcap-momentum && source /home/smallcap-momentum/venv/bin/activate && python refresh.py --yes" >> /home/smallcap-momentum/refresh.log 2>&1
```

---

## Email Monitoring

**File:** `refresh_monitor.py`  
**Email service:** Resend (free tier, sends from `onboarding@resend.dev`)

Each morning after the cron job completes, you receive an email report containing:

1. **Status & Runtime** — Success/failure, total duration, server hostname.
2. **Peak Memory Usage** — Sampled every 2 seconds during the run via a background thread reading `/proc/meminfo`. Shows the highest RAM usage observed, headroom at that peak, and peak swap usage. Warnings at 75% (watch) and 90% (upgrade). Any swap usage is flagged — it means the droplet is memory-constrained.
3. **Data Files Table** — Each source shown as REFRESHED / UNCHANGED / MISSING / CREATED, with its expected refresh cadence, file size, and last-modified timestamp.
4. **Error Log** — Parsed from pipeline output. Catches Python tracebacks (with source file and context), API rate limits (429s), network timeouts, connection errors, and OOM kills. Each error includes severity (CRITICAL/ERROR/WARNING), source, message, and diagnostic context.

### Testing the email

```bash
# Dry run — runs pipeline, saves HTML preview instead of emailing
python refresh_monitor.py --yes --dry-run

# Full test — runs pipeline and sends actual email
python refresh_monitor.py --yes
```

---

## Configuration

**File:** `config.json`

```json
{
  "universe": {
    "min_market_cap": 500000000,
    "max_market_cap": 2000000000
  },
  "signal_weights": {
    "price_momentum": 0.20,
    "volume_surge": 0.20,
    "price_acceleration": 0.10,
    "rsi": 0.00,
    "stochastic": 0.10,
    "financial_health": 0.15,
    "news_attention": 0.05,
    "insider_activity": 0.20
  }
}
```

- **Universe bounds** are enforced during universe building and again when loading data for signal scoring. Changing them here affects both the next universe refresh and how the watchlist filters tickers.
- **Signal weights** must sum to 1.0. Set any signal to 0.00 to disable it. These are the default weights used by the runner; the frontend allows live recalculation with different weights (via `/api/recalc`) without changing the file.

---

## Data Files

All stored in `data/` (gitignored). Format is Apache Parquet throughout.

| File | Description | Size (approx) |
|------|-------------|---------------|
| `universe.parquet` | Filtered tickers within market cap bounds | ~500 rows |
| `all_market_caps.parquet` | Full snapshot of all US common stock market caps | ~6,000 rows |
| `prices_combined.parquet` | 5 years daily OHLCV for all universe tickers | ~1.5M rows |
| `prices/` | Individual ticker price files (used during fetch) | ~1,600 files |
| `fundamentals.parquet` | SEC EDGAR financial ratios and filing dates | ~1,300 rows |
| `news_attention.parquet` | 30-day and 7-day article counts per ticker | ~1,600 rows |
| `insider_activity.parquet` | Form 4 buy/sell counts and dollar values (90-day) | ~1,600 rows |
| `watchlist.parquet` | Final scored and ranked output | ~500 rows |
| `ticker_cik_map.json` | SEC ticker-to-CIK mapping (cached 7 days) | ~6,000 entries |

---

## Environment Variables

Stored in `.env` at the project root (gitignored).

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYGON_API_KEY` | Yes | Polygon.io API key for prices, universe, and news data |
| `SEC_USER_AGENT` | No | User-Agent for SEC EDGAR requests (defaults to `SmallCapMomentum contact@example.com`) |
| `RESEND_API_KEY` | Yes (for monitoring) | Resend API key for daily email reports |

---

## Local Development

### Backend

```bash
cd smallcap-momentum
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env with your API keys
echo 'POLYGON_API_KEY=your_key' > .env

# Build initial data (takes ~30 min first time)
python refresh.py --force

# Start API server
uvicorn api:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # starts at localhost:5173, proxies API to localhost:8000
```

### Useful commands

```bash
# Check data freshness
python refresh.py --status

# Refresh only stale data
python refresh.py

# Force refresh everything
python refresh.py --force

# Run signals only (skip data fetch)
python refresh.py --signals-only

# Skip specific slow fetches
python refresh.py --skip-insider --skip-fundamentals

# Test a single signal
python -m src.signals.price_momentum
python -m src.signals.financial_health

# Test API connection
python src/data/test_connection.py
```

---

## Troubleshooting

**Pipeline takes too long:** On a full `--force` refresh, the bottleneck is SEC EDGAR fetches (fundamentals + insider) due to their 10 req/sec rate limit. A full run from scratch can take 30+ minutes. Smart refresh (default) skips fresh data and typically completes in 3–5 minutes.

**Missing data for a signal:** If a signal scores fewer tickers than the universe, it's because some tickers lack sufficient price history (need 60+ days for most signals). This is normal for recent IPOs or thinly traded stocks. Missing signals are treated as neutral (score 50) in the composite.

**API rate limits (429 errors):** Polygon has generous limits on the Starter plan. SEC EDGAR enforces 10 req/sec — the scripts use a 0.15s delay between requests to stay within this. If you see 429s in the error log, the delay may need increasing.

**OOM / high memory:** Check the daily email's memory panel. If peak RAM consistently exceeds 75% or swap is in use, consider upgrading the droplet. The biggest memory consumer is loading `prices_combined.parquet` (~1.5M rows) into pandas.

**Email not arriving:** Verify `RESEND_API_KEY` in `.env` on the server. Test with `python refresh_monitor.py --yes --dry-run` to confirm the pipeline runs, then without `--dry-run` to test email delivery. Check the Resend dashboard for delivery status.

**Frontend shows stale data:** The frontend fetches from the API on load. If the API is serving old data, check that the cron is running (`crontab -l`) and review `refresh.log` for errors. You can also trigger a manual refresh via the UI's refresh button or by hitting `POST /api/refresh`.

**Git workflow:** The `data/` directory and `.env` are gitignored. Code changes should be committed locally, pushed, then pulled on the server. The cron and `.env` are configured directly on the server and not tracked in git.
