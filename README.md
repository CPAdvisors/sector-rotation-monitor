# Sector Rotation Monitor

NIFTY sector indices vs NIFTY 50, refreshed daily. Static GitHub Pages site; data is
pulled by a scheduled GitHub Action (not fetched live in the browser — see "How it
works" below).

## Setup (one-time)

1. Create a new GitHub repo (e.g. `sector-rotation-monitor`) and push this folder to it,
   or add it as a subfolder of an existing Pages repo (e.g. alongside `NS100/`).
2. In the repo, go to **Settings → Pages** and set the source to the `main` branch
   (root, or the relevant subfolder if nested).
3. Go to **Settings → Actions → General → Workflow permissions** and select
   **"Read and write permissions"** — the workflow needs this to commit the refreshed
   JSON back to the repo.
4. Go to the **Actions** tab → "Update sector performance data" → **Run workflow**
   to populate `data/sector_performance.json` for the first time (don't wait for the
   schedule). Reload the page once it finishes (~1-2 min).

After that, it runs automatically every weekday at 18:00 IST (12:30 UTC), after the
NSE close, and commits the updated JSON. No further action needed.

## How it works

- `scripts/fetch_data.py` — pulls daily closes for NIFTY 50 and 11 NSE sector indices
  via `yfinance`, computes 3M/6M/1Y/3Y cumulative return + alpha vs NIFTY 50 for each,
  and writes `data/sector_performance.json`. If a symbol fails to fetch on a given run,
  it falls back to the last good value for that symbol (flagged `stale` in the JSON)
  rather than dropping it from the page.
- `.github/workflows/update-data.yml` — runs the script on a schedule and commits the
  result. This is what makes the page "live" — the page itself is static and just reads
  whatever JSON is currently in the repo.
- `index.html` — reads `data/sector_performance.json` and renders the leaderboard strip,
  heatmap, and line chart. Pure HTML/CSS/JS + Chart.js from CDN, no build step.

## Adjusting the sector list or schedule

- Sectors/tickers: edit the `SECTORS` list in `scripts/fetch_data.py`. Currently covers 14
  sectors — the 11 original NSE sectoral indices plus 3 newer ones confirmed to have
  Yahoo Finance coverage (Healthcare, Oil & Gas, Consumer Durables). Other newer thematic
  NSE indices (Defence, EV, Housing, Rural, Transportation & Logistics, etc.) were not
  added because Yahoo Finance coverage for them is unconfirmed/inconsistent — check
  finance.yahoo.com for the exact ticker before adding one, since a wrong ticker will just
  silently fail that sector's fetch (handled gracefully, but worth avoiding).
- Refresh schedule: edit the `cron` line in `.github/workflows/update-data.yml`
  (currently weekdays only, since sector indices don't move on weekends).

## Known caveats

- `yfinance` scrapes an undocumented Yahoo endpoint; it can occasionally return empty
  data for a symbol on a given run. The stale-fallback logic handles this, but if a
  sector shows `stale` for several days in a row, check the Action's run log.
- A couple of the smaller sector indices (Media, PSU Bank, Realty) have historically
  had thinner Yahoo Finance coverage than the large ones — worth spot-checking their
  numbers against NSE's published values after the first run.
- Windows are calendar-day lookbacks (91/182/365/1095 days) mapped to the nearest prior
  trading day, not fixed trading-day counts — this matches how CAGR is usually quoted
  but means the exact window can drift by a day or two around holidays.
