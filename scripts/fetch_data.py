"""
Fetch NIFTY 50 and NSE sector index data via yfinance, compute trailing
performance over 3M / 6M / 1Y / 3Y windows, and write a JSON payload that the
static site (index.html) reads to render the heatmap and line chart.

Run daily (after market close) via .github/workflows/update-data.yml.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd

# --- Universe -----------------------------------------------------------
BENCHMARK = {"symbol": "^NSEI", "name": "NIFTY 50"}

SECTORS = [
    {"symbol": "^CNXIT", "name": "NIFTY IT"},
    {"symbol": "^CNXAUTO", "name": "NIFTY AUTO"},
    {"symbol": "^CNXPHARMA", "name": "NIFTY PHARMA"},
    {"symbol": "^CNXFMCG", "name": "NIFTY FMCG"},
    {"symbol": "^CNXMETAL", "name": "NIFTY METAL"},
    {"symbol": "^CNXENERGY", "name": "NIFTY ENERGY"},
    {"symbol": "^NSEBANK", "name": "NIFTY BANK"},
    {"symbol": "NIFTY_FIN_SERVICE.NS", "name": "NIFTY FIN SERVICE"},
    {"symbol": "^CNXREALTY", "name": "NIFTY REALTY"},
    {"symbol": "^CNXMEDIA", "name": "NIFTY MEDIA"},
    {"symbol": "^CNXPSUBANK", "name": "NIFTY PSU BANK"},
    # Newer NSE sector indices (launched within the last ~5-8 years) confirmed
    # to have Yahoo Finance coverage:
    {"symbol": "NIFTY_HEALTHCARE.NS", "name": "NIFTY HEALTHCARE"},
    {"symbol": "NIFTY_OIL_AND_GAS.NS", "name": "NIFTY OIL & GAS"},
    {"symbol": "NIFTY_CONSR_DURBL.NS", "name": "NIFTY CONSUMER DURABLES"},
]

ALL_SERIES = [BENCHMARK] + SECTORS

# Windows in trading-relevant calendar days, mapped to output keys
WINDOWS = [
    ("3M", 91),
    ("6M", 182),
    ("1Y", 365),
    ("3Y", 365 * 3),
]

LOOKBACK_PERIOD = "5y"  # pad beyond 3Y so the 3Y window always has a start point
MAX_RETRIES = 3
RETRY_SLEEP_SEC = 5


def fetch_history(symbol: str) -> pd.DataFrame:
    """Fetch daily close history for a symbol, with retries and a period fallback.

    Some NSE sector indices (particularly newer/thinner ones) are served by
    Yahoo with a shorter history than the requested period, even when data
    technically exists further back. If the first attempt comes back short,
    retry once with period="max" before giving up.
    """
    last_err = None
    for period in (LOOKBACK_PERIOD, "max"):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
                if df is not None and not df.empty:
                    span_days = (df.index[-1] - df.index[0]).days
                    print(f"  {symbol}: got {len(df)} rows, {df.index[0].date()} to {df.index[-1].date()} ({span_days}d span, period={period})")
                    if span_days >= 95:  # enough for at least the 3M window
                        return df
                    last_err = RuntimeError(f"only {span_days}d of history returned for {symbol} (period={period})")
                    break  # short history isn't a flake — try the next period instead of retrying
                last_err = RuntimeError(f"empty dataframe for {symbol} (period={period})")
            except Exception as e:  # noqa: BLE001
                last_err = e
            print(f"  retry {attempt}/{MAX_RETRIES} for {symbol} (period={period}): {last_err}", file=sys.stderr)
            time.sleep(RETRY_SLEEP_SEC)
    raise RuntimeError(f"Failed to fetch usable history for {symbol}: {last_err}")


def nearest_on_or_before(closes: pd.Series, target_date) -> tuple | None:
    """Return (date, close) for the last available trading day <= target_date."""
    eligible = closes[closes.index <= target_date]
    if eligible.empty:
        return None
    d = eligible.index[-1]
    return d, float(eligible.iloc[-1])


def compute_returns(closes: pd.Series) -> dict:
    """Compute cumulative % return and annualized CAGR for each window."""
    latest_date = closes.index[-1]
    latest_close = float(closes.iloc[-1])
    out = {}
    for label, days in WINDOWS:
        target = latest_date - pd.Timedelta(days=days)
        found = nearest_on_or_before(closes, target)
        if found is None:
            out[label] = {"return_pct": None, "cagr_pct": None, "start_date": None}
            continue
        start_date, start_close = found
        cum_return = (latest_close / start_close) - 1.0
        years = days / 365.0
        cagr = (latest_close / start_close) ** (1.0 / years) - 1.0 if years > 0 else None
        out[label] = {
            "return_pct": round(cum_return * 100, 2),
            "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
            "start_date": start_date.strftime("%Y-%m-%d"),
        }
    return out


def build_series_payload(meta: dict, df: pd.DataFrame) -> dict:
    closes = df["Close"].dropna()
    returns = compute_returns(closes)
    # Downsample the daily close history for the line chart (weekly points is
    # plenty for a 3Y view and keeps the JSON small).
    weekly = closes.resample("W-FRI").last().dropna()
    history = [
        {"date": d.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
        for d, v in weekly.items()
    ]
    return {
        "symbol": meta["symbol"],
        "name": meta["name"],
        "latest_close": round(float(closes.iloc[-1]), 2),
        "latest_date": closes.index[-1].strftime("%Y-%m-%d"),
        "returns": returns,
        "history": history,
    }


def load_previous(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def find_previous_series(prev: dict | None, symbol: str) -> dict | None:
    if not prev:
        return None
    if prev.get("benchmark", {}).get("symbol") == symbol:
        return prev["benchmark"]
    for s in prev.get("sectors", []):
        if s.get("symbol") == symbol:
            return s
    return None


def main():
    out_path = "data/sector_performance.json"
    previous = load_previous(out_path)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": None,
        "sectors": [],
        "errors": [],
    }

    for meta in ALL_SERIES:
        print(f"Fetching {meta['symbol']} ({meta['name']})...")
        try:
            df = fetch_history(meta["symbol"])
            series_payload = build_series_payload(meta, df)
            series_payload["stale"] = False
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {e}", file=sys.stderr)
            payload["errors"].append({"symbol": meta["symbol"], "name": meta["name"], "error": str(e)})
            fallback = find_previous_series(previous, meta["symbol"])
            if fallback is None:
                continue
            series_payload = fallback
            series_payload["stale"] = True

        if meta is BENCHMARK:
            payload["benchmark"] = series_payload
        else:
            payload["sectors"].append(series_payload)

    if payload["benchmark"] is None:
        raise SystemExit("Benchmark (NIFTY 50) fetch failed — aborting so we don't overwrite good data with nothing.")

    # Compute alpha (sector return - benchmark return) for each window, for each sector
    bench_returns = payload["benchmark"]["returns"]
    for sec in payload["sectors"]:
        sec["alpha"] = {}
        for label, _ in WINDOWS:
            sec_r = sec["returns"][label]["return_pct"]
            bench_r = bench_returns[label]["return_pct"]
            if sec_r is None or bench_r is None:
                sec["alpha"][label] = None
            else:
                sec["alpha"][label] = round(sec_r - bench_r, 2)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    ok = len(payload["sectors"])
    total = len(SECTORS)
    print(f"\nWrote {out_path}: {ok}/{total} sectors, {len(payload['errors'])} errors.")
    if payload["errors"]:
        for e in payload["errors"]:
            print(f"  - {e['name']} ({e['symbol']}): {e['error']}")


if __name__ == "__main__":
    main()
