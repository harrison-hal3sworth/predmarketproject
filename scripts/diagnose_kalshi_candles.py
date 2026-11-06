# Contains Claude generated code

"""
diagnose_kalshi_candles.py

Why does fetch_kalshi_early_prices.py get zero candles? Tries one known
ticker against Kalshi's candlestick endpoints with different windows and
resolutions, and prints the HTTP status and the start of each raw response.
Makes about 12 requests, takes ~15 seconds, saves nothing.

    python scripts/diagnose_kalshi_candles.py

Send the full output to Claude.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.fetchers.kalshi_history import BASE_URL  # noqa: E402

# One game the original pipeline DID get a candle for, so we know data existed.
raw = pd.read_csv(ROOT / "data" / "nba_2025_historical.csv")
row = raw.dropna(subset=["kalshi_home_ticker", "kalshi_home_candle_end_ts"]).iloc[len(raw) // 2]
ticker = row["kalshi_home_ticker"]
kick = int(pd.Timestamp(row["start_time_utc"]).timestamp())
series = ticker.split("-")[0]
print(f"BASE_URL = {BASE_URL}")
print(f"ticker   = {ticker}   kickoff = {row['start_time_utc']} ({kick})")
print(f"pipeline's stored candle_end_ts = {int(row['kalshi_home_candle_end_ts'])}, "
      f"bid/ask then = {row['kalshi_home_yes_bid_close']}/{row['kalshi_home_yes_ask_close']}\n")


def show(label, url, params):
    try:
        r = requests.get(url, params=params, timeout=30)
        body = r.text
        try:
            js = r.json()
            n = len(js.get("candlesticks", []) or []) if isinstance(js, dict) else "?"
            keys = list(js.keys()) if isinstance(js, dict) else type(js).__name__
        except ValueError:
            n, keys = "?", "not JSON"
        print(f"[{label}] HTTP {r.status_code}  candles={n}  keys={keys}")
        print(f"    {body[:300]!r}\n")
    except requests.RequestException as e:
        print(f"[{label}] request failed: {e}\n")


hist = f"{BASE_URL}/historical/markets/{ticker}/candlesticks"
live = f"{BASE_URL}/series/{series}/markets/{ticker}/candlesticks"

tests = [
    ("A  exact pipeline call: 1-min, 30 min before kickoff", hist, kick - 1800, kick, 1),
    ("B  1-min, 2 h window ending T-2h", hist, kick - 4 * 3600, kick - 2 * 3600, 1),
    ("C  1-min, 6 h window", hist, kick - 6 * 3600, kick, 1),
    ("D  1-min, 25 h window (what the script used)", hist, kick - 25 * 3600, kick, 1),
    ("E  hourly, 25 h window (first version)", hist, kick - 25 * 3600, kick, 60),
    ("F  daily, whole market life", hist, kick - 30 * 86400, kick, 1440),
    ("G  live/series path, 1-min, 30 min", live, kick - 1800, kick, 1),
    ("H  live/series path, hourly, 25 h", live, kick - 25 * 3600, kick, 60),
]
for label, url, s, e, p in tests:
    show(label, url, {"start_ts": s, "end_ts": e, "period_interval": p, "include_latest_before_start": "true"})

show("I  market metadata (historical)", f"{BASE_URL}/historical/markets/{ticker}", {})
show("J  market metadata (live)", f"{BASE_URL}/markets/{ticker}", {})
