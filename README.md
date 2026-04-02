# Unicorn Hunt — Small Cap Momentum Screener

**Live at [unicornpunk.org](https://unicornpunk.org)**

A quantitative small-cap stock screener that combines eight momentum and quality signals to surface US equities in the $500M–$2.5B market cap range showing unusual strength. The system fetches data from multiple sources (Polygon, SEC EDGAR), scores every ticker on a 0–100 percentile scale across each signal, and produces a ranked watchlist updated daily by cron. A separate risk metrics engine computes Sharpe ratios and Information Ratios for every ticker, providing an independent risk-adjusted view of the universe. An activist tracker monitors SEC Schedule 13D filings to flag stocks attracting activist investor attention.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Data Pipeline](#data-pipeline)
- [Signal Engine](#signal-engine)
- [Risk Metrics Engine](#risk-metrics-engine)
- [13D Activist Tracker](#13d-activist-tracker)
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
│  SEC EDGAR 13D ──────┼────▶│  risk_metrics.py  │     │  Pages                 │
│                      │     │  api.py (FastAPI) │◀───▶│                        │
└─────────────────────┘     │                   │     └───────────────────────┘
                             │  Cron (9pm UTC)   │
                             │  refresh_monitor  │──▶  Email via Resend
                             └──────────────────┘
```

The system has five layers:

1. **Data pipeline** — Python scripts that fetch, cache, and combine data from external APIs into Parquet files.
2. **Signal engine** — Eight signal classes that each score every ticker, plus a runner that produces a weighted composite and ranked watchlist.
3. **Risk metrics engine** — Computes Sharpe ratio and two Information Ratios (vs universe and vs Russell 2000) for every ticker, producing an independent risk-adjusted ranking.
4. **13D activist tracker** — Scans SEC EDGAR for Schedule 13D filings (activist investor declarations of 5%+ ownership) against universe tickers.
5. **API + Frontend** — A FastAPI backend serving the momentum watchlist, risk metrics, and activist filings. Consumed by a React single-page app with a retro DOS-terminal aesthetic.

---

## Data Pipeline

Each data source has its own fetch script in `src/data/`. They all write Parquet files to the `data/` directory and support `--refresh` (clear cache) and `--test N` (fetch only first N tickers) flags.

### Universe (`src/data/universe.py`)

Builds the investable universe by fetching all active US common stocks (type "CS") from Polygon, then filtering to the market cap range defined in `config.json` (default $500M–$2.5B). It stores both the full market cap snapshot (`data/all_market_caps.parquet`) and the filtered universe (`data/universe.parquet`). Market caps are always re-fetched fresh — no stale cache reuse — so the bounds are applied against current values.

- **Source:** Polygon `/v3/reference/tickers` + `/v3/reference/tickers/{symbol}`
- **Refresh cadence:** Weekly (7 days)
- **Parallelism:** 10 concurrent async requests with 0.5s batch pause

### Prices (`src/data/fetch_prices.py`)

Fetches 5 years of daily OHLCV data for every ticker in the universe. Individual ticker files are saved to `data/prices/` as they complete, then combined into a single `data/prices_combined.parquet` for the signal engine. On refresh, only missing tickers are fetched (unless `--refresh` forces a full re-fetch).

- **Source:** Polygon `/v2/aggs/ticker/{symbol}/range/1/day`
- **Refresh cadence:** Daily (1 day)
- **Output:** ~2M rows in the combined file covering ~2,000+ tickers × 5 years

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

### 13D Activist Filings (`src/data/fetch_13d.py`)

Scans SEC EDGAR submissions for recent Schedule 13D and 13D/A filings — declarations by investors who have acquired 5%+ ownership in a company with intent to influence management. For each ticker in the universe, checks the company's recent filing history for activist-related form types.

- **Source:** SEC EDGAR `data.sec.gov/submissions/CIK{cik}.json`
- **Refresh cadence:** Weekly (7 days)
- **Lookback:** 90 days of filings
- **Rate limit:** Same SEC 10/sec policy
- **Form types matched:** `SC 13D`, `SC 13D/A`, `SCHEDULE 13D`, `SCHEDULE 13D/A`
- **Output:** `data/13d_filings.parquet`

A Schedule 13D filing is significant because it signals an activist investor taking a meaningful position with the intent to push for changes — board seats, strategic alternatives, operational restructuring, or M&A. An initial `SC 13D` filing is more notable than an amendment (`SC 13D/A`), which may just reflect routine position updates.

### Benchmark (`src/signals/risk_metrics.py`)

Fetches daily OHLCV data for IWM (iShares Russell 2000 ETF) from Polygon as an external benchmark for the Information Ratio calculation. Stored in its own Parquet file, completely separate from the main price pipeline.

- **Source:** Polygon `/v2/aggs/ticker/IWM/range/1/day`
- **Refresh cadence:** Daily (1 day)
- **Output:** `data/benchmark_iwm.parquet`

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

## Risk Metrics Engine

The risk metrics engine (`src/signals/risk_metrics.py`) provides an independent risk-adjusted view of the universe, separate from the momentum signal scoring. It computes three metrics for every ticker and blends them into a composite ranking.

### Metrics

| Metric | Default Weight | What it measures |
|--------|---------------|-----------------|
| **Sharpe Ratio** | 34% | Absolute risk-adjusted return. Mean daily return divided by daily return volatility, annualised (×√252). Higher = better return per unit of risk. |
| **IR (Universe)** | 33% | Information Ratio vs the equal-weighted universe average. Each day, benchmark return = mean return across all tickers. Measures how consistently a stock outperforms the other stocks in your filtered universe. |
| **IR (Russell 2000)** | 33% | Information Ratio vs IWM (Russell 2000 ETF). Measures how consistently a stock outperforms the broader small-cap market. Uses separately fetched benchmark data (`data/benchmark_iwm.parquet`). |

### How it works

1. Loads `prices_combined.parquet` and computes a daily returns matrix (dates × tickers).
2. Fetches IWM benchmark data from Polygon (or uses cached copy if fresh).
3. For each ticker, computes the three metrics over a configurable lookback window (default 63 trading days, ~3 months).
4. Converts each metric to a percentile rank (0–100) within the universe.
5. Blends percentile ranks using configurable weights from `risk_metrics_config.json`.
6. Outputs `data/risk_metrics.parquet` with raw values, percentiles, composite score, and rank.

### Why two Information Ratios?

The universe-based IR and the Russell-based IR answer different questions and produce meaningfully different rankings:

- **IR (Universe)** measures outperformance against your own stock pool. The benchmark is the equal-weighted average return of ~1,000 stocks in the $500M–$2.5B range.
- **IR (Russell 2000)** measures outperformance against the broader small-cap market (IWM), which includes stocks well below $500M and has different sector weightings.

A stock ranking high on both IRs is outperforming regardless of benchmark choice — a robust signal. A stock ranking high on one but not the other tells you its edge is specific to either your universe's composition or the broader market.

### Configuration (`risk_metrics_config.json`)

```json
{
  "lookback_days": 63,
  "benchmark_ticker": "IWM",
  "benchmark_staleness_days": 1,
  "weights": {
    "sharpe": 0.34,
    "ir_universe": 0.33,
    "ir_russell": 0.33
  }
}
```

- **lookback_days** — rolling window in trading days (63 ≈ 3 months).
- **benchmark_ticker** — ETF ticker for the external benchmark.
- **weights** — must sum to 1.0. Adjust to emphasise absolute returns (Sharpe) vs relative performance (IRs).

### Running standalone

```bash
python -m src.signals.risk_metrics --save          # compute and save
python -m src.signals.risk_metrics --save --refresh # force re-fetch IWM
```

---

## 13D Activist Tracker

The activist tracker (`src/data/fetch_13d.py`) monitors SEC EDGAR for Schedule 13D filings against universe tickers. These filings are required when an investor acquires 5% or more of a company's shares with the intent to influence or change control — making them a strong signal of activist interest.

### What it tracks

- **SC 13D** — Initial filings. An activist has taken a 5%+ position and declared intent to influence the company. This is the high-signal event.
- **SC 13D/A** — Amendments to existing 13D filings. May reflect position increases, updated intentions, or routine updates. Less significant than initial filings but still worth monitoring.

### How it works

1. Loads the universe and the ticker-to-CIK mapping (same mapping used by the insider and fundamentals fetchers).
2. For each ticker, fetches the company's recent submissions from SEC EDGAR.
3. Filters for 13D-related form types filed within the 90-day lookback window.
4. Saves matched filings to `data/13d_filings.parquet`.
5. The API enriches filings with sector, market cap, price, and 7-day change from the universe and price data.

### Frontend display

The activist tracker appears in a magenta-themed DOS terminal below the risk metrics section. Each filing shows the date, ticker, form type (initial vs amendment), price, 7-day change, market cap, and company name. Overlap indicators show if the stock also appears in the momentum top 20 (★) or risk-adjusted top 20 (◆).

### Running standalone

```bash
python -m src.data.fetch_13d              # fetch and save
python -m src.data.fetch_13d --refresh    # clear cache and re-fetch
python -m src.data.fetch_13d --test 20    # test with first 20 tickers
```

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
| `GET` | `/api/risk-metrics-config` | Risk metrics weights and lookback from `risk_metrics_config.json` |
| `GET` | `/api/watchlist` | Full ranked watchlist with all scores, fundamentals, insider data, and price info |
| `GET` | `/api/risk-metrics` | Ranked risk-adjusted metrics with Sharpe, IR-Universe, IR-Russell, price info |
| `GET` | `/api/13d-filings` | Recent 13D activist filings enriched with sector, market cap, and price info |
| `GET` | `/api/prices/{ticker}` | Daily close prices for a single ticker (default 365 days) |
| `POST` | `/api/recalc` | Re-rank watchlist with custom signal weights (body: `{"weights": {...}}`) — instant, no refetch |
| `POST` | `/api/recalc-risk` | Re-rank risk metrics with custom metric weights (body: `{"weights": {...}}`) — instant, no refetch |
| `POST` | `/api/refresh` | Trigger a background smart refresh (skips fresh data) |
| `POST` | `/api/reset` | Trigger a background force refresh (re-fetches everything) |
| `GET` | `/api/progress` | Poll refresh progress (step, percentage, detail) |

CORS is configured for `localhost:3000`, `localhost:5173`, `unicornpunk.org`, and `smallcap-momentum.pages.dev`.

### Recalc Architecture

Both `/api/recalc` and `/api/recalc-risk` follow the same pattern:

1. Frontend sends adjusted weights via POST.
2. Backend reads the existing Parquet file (which contains individual signal/metric scores).
3. Recomputes composite scores using the new weights. Missing values are treated as neutral (50 for signals, excluded from risk metrics).
4. Re-ranks, overwrites the Parquet file, and returns the enriched response.

This enables instant re-weighting without re-fetching any data. The weights are ephemeral — the next cron run reverts to the defaults from the JSON config files.

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
**Style:** Retro DOS-terminal aesthetic with neon orange/cyan/magenta on dark backgrounds

### Key Sections

The page is divided into three major sections, each with its own colour theme:

**Quant Signals (orange theme)**
- **ControlPanel** — Displays market cap range (read-only, set via `config.json`) and universe count.
- **QuantSignalsSection** — Interactive sliders for each of the 8 signal weights. Adjusting one automatically rebalances others to maintain 100% total. Recalc button sends adjusted weights to `/api/recalc`.
- **DOSTerminal** — The main output: top-20 ranked stocks in a monospaced terminal format. Click any row to expand a detail panel showing fundamentals, insider activity, and price info. Includes CSV export.

**Risk Metrics (cyan theme)**
- **RiskMetricsSection** — Three sliders for Sharpe, IR-Universe, and IR-Russell weights. Recalc button sends adjusted weights to `/api/recalc-risk`.
- **RiskMetricsTerminal** — Top-20 ranked stocks by risk-adjusted composite in a teal-themed DOS terminal. Shows raw Sharpe and IR values. Click to expand percentile breakdown. Stocks also appearing in the momentum top 20 are marked with a ★ indicator.

**Activist Tracker (magenta theme)**
- **ActivistFilingsSection** — Header for the 13D tracker section.
- **ActivistFilingsTerminal** — Recent Schedule 13D filings in a magenta-themed DOS terminal. Shows filing date, ticker, form type (initial vs amendment), price, 7-day change, market cap, and company name. Overlap indicators: ★ = also in momentum top 20, ◆ = also in risk-adjusted top 20.

### Pharma Filter

The momentum and risk metrics terminals include a toggle to exclude pharmaceutical stocks (SIC sector code `28`, displayed as `PHRM`). This is a frontend-only filter — no backend changes or separate watchlists are needed, since the API returns the full ranked universe and the frontend filters before slicing to top 20.

When active:
- Both terminals show "ex. Pharma" in their title bars and command lines (e.g. `run_signals.exe --exclude-pharma`)
- The toggle shows how many pharma stocks were removed from the top 20
- Original ranks from the full universe are preserved (gaps in numbering indicate where pharma stocks sat)
- CSV export respects the filter and appends `_ex_pharma` to the filename
- The ★ momentum overlap indicator on the risk terminal uses the filtered top 20

The toggle state is shared — clicking it on either terminal toggles both.

**Why pharma filtering?** Small-cap pharma stocks frequently dominate momentum screeners because they move on binary catalysts (FDA approvals, trial data) rather than the kind of sustained momentum the signals are designed to capture. Filtering them out reveals the non-pharma stocks that might otherwise be buried.

### Build & Deploy

```bash
cd frontend
npm install
npm run dev          # local dev server at localhost:5173
npm run build        # production build to frontend/dist/
```

The `VITE_API_URL` environment variable points to the backend. Set it in Cloudflare Pages environment settings for production (e.g., `https://api.unicornpunk.org`).

For local development pointed at the live server:
```bash
VITE_API_URL=https://api.unicornpunk.org npm run dev
```

---

## Server & Deployment

### Infrastructure

- **Server:** DigitalOcean Droplet (Linux/Ubuntu 24.04)
- **Hostname:** `unicorn-hunt` (internal)
- **Domain:** `unicornpunk.org` (frontend), `api.unicornpunk.org` (API)
- **Frontend hosting:** Cloudflare Pages (separate from the droplet)
- **Reverse proxy:** Caddy (auto HTTPS for `api.unicornpunk.org` → localhost:8000)

### Server Setup

The backend runs inside a Python virtual environment at `/home/smallcap-momentum/`:

```
/home/smallcap-momentum/
├── api.py                      # FastAPI backend
├── refresh.py                  # Data refresh pipeline
├── refresh_monitor.py          # Monitoring wrapper (sends email reports)
├── config.json                 # Universe bounds + signal weights
├── risk_metrics_config.json    # Risk metrics weights + lookback config
├── .env                        # API keys (not in git)
├── requirements.txt
├── venv/                       # Python 3.12 virtual environment
├── data/                       # All Parquet data files (not in git)
│   ├── universe.parquet
│   ├── all_market_caps.parquet
│   ├── prices_combined.parquet
│   ├── prices/                 # Individual ticker price files
│   ├── fundamentals.parquet
│   ├── news_attention.parquet
│   ├── insider_activity.parquet
│   ├── watchlist.parquet
│   ├── benchmark_iwm.parquet   # IWM daily prices for IR calculation
│   ├── risk_metrics.parquet    # Risk-adjusted rankings
│   ├── 13d_filings.parquet     # Activist investor filings
│   └── ticker_cik_map.json
├── src/
│   ├── data/                   # Data fetch modules
│   └── signals/                # Signal scoring + risk metrics modules
├── frontend/                   # React app (deployed separately to Cloudflare)
└── refresh.log                 # Cron output log
```

### Process Management

The FastAPI server runs as a systemd service:

```bash
sudo systemctl restart unicornhunt   # restart
sudo systemctl status unicornhunt    # check status
sudo journalctl -u unicornhunt -n 50 # view logs
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
   - **Always runs:** News (daily), Signals (every run), Risk Metrics (every run)
   - **Runs if stale:** Prices (daily), Insider (14d), Universe (7d), Fundamentals (30d), Benchmark IWM (daily), 13D Filings (7d)
3. After signals, `risk_metrics.py` runs to recompute Sharpe and IR rankings.
4. `fetch_13d.py` runs to scan for new activist filings (weekly cadence).
5. After the pipeline completes, `refresh_monitor.py` compares file timestamps to determine what was actually updated, captures peak memory usage and errors, and sends an email report via Resend.

### Typical daily run

On a day where only news and signals need updating (prices are fresh from the previous run), the pipeline takes approximately 3–5 minutes. Risk metrics add ~5 seconds. 13D filings add ~10–15 minutes when stale (weekly), as it checks every ticker in the universe.

### Editing the cron

```bash
crontab -e          # edit
crontab -l          # verify
```

### Checking the log

```bash
tail -100 /home/smallcap-momentum/refresh.log
```

---

## Email Monitoring

**File:** `refresh_monitor.py`
**Email service:** Resend (free tier, sends from `onboarding@resend.dev`)

Each morning after the cron job completes, you receive two emails:

### 1. Refresh Report

Contains:

1. **Status & Runtime** — Success/failure, total duration, server hostname.
2. **Peak Memory Usage** — Sampled every 2 seconds during the run via a background thread reading `/proc/meminfo`. Shows the highest RAM usage observed, headroom at that peak, and peak swap usage. Warnings at 75% (watch) and 90% (upgrade). Any swap usage is flagged — it means the droplet is memory-constrained.
3. **Data Files Table** — Each source shown as REFRESHED / UNCHANGED / MISSING / CREATED, with its expected refresh cadence, file size, and last-modified timestamp. Includes `benchmark_iwm.parquet`, `risk_metrics.parquet`, and `13d_filings.parquet`.
4. **Error Log** — Parsed from pipeline output. Catches Python tracebacks (with source file and context), API rate limits (429s), network timeouts, connection errors, and OOM kills. Each error includes severity (CRITICAL/ERROR/WARNING), source, message, and diagnostic context.

### 2. Watchlist Email

Shows the top 20 ranked stocks with composite scores, signal pills, price info, sector, market cap, and insider activity indicators.

### Testing

```bash
# Dry run — runs pipeline, saves HTML preview instead of emailing
python refresh_monitor.py --yes --dry-run

# Full test — runs pipeline and sends actual email
python refresh_monitor.py --yes
```

---

## Configuration

### Signal Weights (`config.json`)

```json
{
  "universe": {
    "min_market_cap": 500000000,
    "max_market_cap": 2500000000
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

### Risk Metrics Weights (`risk_metrics_config.json`)

```json
{
  "lookback_days": 63,
  "benchmark_ticker": "IWM",
  "benchmark_staleness_days": 1,
  "weights": {
    "sharpe": 0.34,
    "ir_universe": 0.33,
    "ir_russell": 0.33
  }
}
```

- **lookback_days** — number of trading days for the rolling calculation window (63 ≈ 3 months).
- **benchmark_ticker** — ETF used for the external IR calculation.
- **weights** — must sum to 1.0. The frontend allows live recalculation via `/api/recalc-risk`.

---

## Data Files

All stored in `data/` (gitignored). Format is Apache Parquet throughout.

| File | Description | Size (approx) |
|------|-------------|---------------|
| `universe.parquet` | Filtered tickers within market cap bounds | ~1,100 rows |
| `all_market_caps.parquet` | Full snapshot of all US common stock market caps | ~6,000 rows |
| `prices_combined.parquet` | 5 years daily OHLCV for all universe tickers | ~2M rows |
| `prices/` | Individual ticker price files (used during fetch) | ~2,000 files |
| `fundamentals.parquet` | SEC EDGAR financial ratios and filing dates | ~1,300 rows |
| `news_attention.parquet` | 30-day and 7-day article counts per ticker | ~1,100 rows |
| `insider_activity.parquet` | Form 4 buy/sell counts and dollar values (90-day) | ~1,100 rows |
| `watchlist.parquet` | Final scored and ranked momentum output | ~1,100 rows |
| `benchmark_iwm.parquet` | IWM daily OHLCV for IR-Russell calculation | ~1,250 rows |
| `risk_metrics.parquet` | Sharpe, IR-Universe, IR-Russell + composite rank | ~900 rows |
| `13d_filings.parquet` | Recent SC 13D/13D-A activist filings | varies |
| `ticker_cik_map.json` | SEC ticker-to-CIK mapping (cached 7 days) | ~6,000 entries |

---

## Environment Variables

Stored in `.env` at the project root (gitignored).

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYGON_API_KEY` | Yes | Polygon.io API key for prices, universe, news, and benchmark data |
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
npm run dev    # starts at localhost:5173, defaults to API at localhost:8000
```

To point local frontend at the live server:
```bash
VITE_API_URL=https://api.unicornpunk.org npm run dev
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

# Run risk metrics standalone
python -m src.signals.risk_metrics --save
python -m src.signals.risk_metrics --save --refresh  # force IWM re-fetch

# Fetch 13D filings standalone
python -m src.data.fetch_13d
python -m src.data.fetch_13d --refresh    # force re-fetch
python -m src.data.fetch_13d --test 20    # test mode

# Test API connection
python src/data/test_connection.py
```

---

## Troubleshooting

**Pipeline takes too long:** On a full `--force` refresh, the bottleneck is SEC EDGAR fetches (fundamentals + insider + 13D) due to their 10 req/sec rate limit. A full run from scratch can take 30+ minutes. Smart refresh (default) skips fresh data and typically completes in 3–5 minutes.

**Missing data for a signal:** If a signal scores fewer tickers than the universe, it's because some tickers lack sufficient price history (need 60+ days for most signals). This is normal for recent IPOs or thinly traded stocks. Missing signals are treated as neutral (score 50) in the composite.

**Risk metrics scores fewer tickers than the watchlist:** Risk metrics require 70% data coverage over the lookback window (44 of 63 days). Tickers with thin trading history or recent IPOs may not meet this threshold and are excluded.

**API rate limits (429 errors):** Polygon has generous limits on the Starter plan. SEC EDGAR enforces 10 req/sec — the scripts use a 0.15s delay between requests to stay within this. If you see 429s in the error log, the delay may need increasing.

**OOM / high memory:** Check the daily email's memory panel. If peak RAM consistently exceeds 75% or swap is in use, consider upgrading the droplet. The biggest memory consumer is loading `prices_combined.parquet` (~2M rows) into pandas.

**Email not arriving:** Verify `RESEND_API_KEY` in `.env` on the server. Test with `python refresh_monitor.py --yes --dry-run` to confirm the pipeline runs, then without `--dry-run` to test email delivery. Check the Resend dashboard for delivery status.

**Frontend shows stale data:** The frontend fetches from the API on load. If the API is serving old data, check that the cron is running (`crontab -l`) and review `refresh.log` for errors. After a cron run, the API must be restarted to serve updated Parquet files: `sudo systemctl restart unicornhunt`.

**Blank page on the frontend:** Check the browser console (right-click → Inspect → Console) for JavaScript errors. Common causes: a variable reference mismatch in the JSX, or the API being unreachable (check `api.unicornpunk.org/api/status` in your browser).

**SSH session killed during long refresh:** Use `screen` to keep the process running after disconnect: `screen -S refresh` then run the command. Reconnect later with `screen -r refresh`. If the screen shows "Attached" from a dead session, run `screen -wipe` first.

**Git workflow:** The `data/` directory and `.env` are gitignored. Code changes should be committed locally, pushed, then pulled on the server. The cron and `.env` are configured directly on the server and not tracked in git. Use feature branches for significant changes — test on the server by switching branches, then merge to `main` when validated.
