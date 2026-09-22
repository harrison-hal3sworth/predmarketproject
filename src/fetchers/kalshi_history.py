"""
kalshi_history.py

Pulls historical Kalshi game-winner market data: enumerates settled
markets for a series, then pulls the candlestick nearest a game's
scheduled start time (NOT market close — see the project notes on why
"closing line" for a sports prediction market means pre-kickoff, not
pre-settlement).

Confirmed against live Kalshi data on 2026-09-22 (see chat for the raw
samples this was built from):
  - Market enumeration: GET /trade-api/v2/markets
      params: series_ticker, status ("open" | "settled" | "closed"),
      limit (<=1000 seen working), cursor (pagination token)
  - Candlesticks: GET /trade-api/v2/series/{series_ticker}/markets/{ticker}/candlesticks
      params: start_ts, end_ts (unix seconds), period_interval
      (1 | 60 | 1440, minutes), include_latest_before_start (bool)
      Response candlestick fields: end_period_ts, price.{open,high,low,
      close,mean,previous}_dollars, yes_bid.{open,high,low,close}_dollars,
      yes_ask.{open,high,low,close}_dollars, volume_fp, open_interest_fp
      — all price fields are decimal-dollar STRINGS in [0, 1].

This module only pulls and lightly reshapes raw data — no probability
math happens here. What "the market's price" means (last trade? mean?
midpoint of bid/ask?) is a modeling decision left to your own analysis
code; the picker below returns the whole candle so you can choose.

No auth is required for public market-data endpoints (confirmed working
without an API key). Requires a real, non-sandboxed network connection —
this will NOT work from inside the Claude cloud session; run it from
your own machine.
"""

import time
from datetime import date, datetime, timezone
from typing import Optional

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Basic-tier read budget is 200 tokens/sec, ~10 tokens/GET -> ~20 req/sec
# ceiling. We stay well under that; there's no need to push it for a
# one-sport backfill, and a small delay makes 403s/429s easier to diagnose
# if the account tier turns out to be more limited than documented.
_REQUEST_DELAY_SECONDS = 0.1
_MAX_RETRIES = 4


def _get(path: str, params: dict) -> dict:
    url = f"{BASE_URL}{path}"
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                print(f"  rate limited, backing off {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            time.sleep(_REQUEST_DELAY_SECONDS)
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed after {_MAX_RETRIES} attempts: {last_exc}")


def list_settled_markets(series_ticker: str, page_limit: int = 200) -> list[dict]:
    """Enumerates every settled market in a series, following pagination
    cursors. Returns raw Market objects as Kalshi sends them (ticker,
    event_ticker, result, close_time, etc.) — parsing happens in
    src/matching/event_matcher.py, not here.

    A game's two side-markets (e.g. ...-NYG and ...-LAR) both come back
    as separate entries; downstream code should generally pick one side
    per game deterministically (e.g. always parse the away-team-ticker's
    price, or whichever side the schedule join lands on) rather than
    double-counting both.
    """
    markets = []
    cursor = None
    while True:
        params = {"series_ticker": series_ticker, "status": "settled", "limit": page_limit}
        if cursor:
            params["cursor"] = cursor
        payload = _get("/markets", params)
        page = payload.get("markets", [])
        markets.extend(page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
    return markets


def get_candlesticks(
    series_ticker: str,
    ticker: str,
    start_ts: int,
    end_ts: int,
    period_interval: int = 1,
    include_latest_before_start: bool = True,
) -> list[dict]:
    """Raw candlestick pull for one market ticker over [start_ts, end_ts]
    (unix seconds). period_interval is in minutes: 1, 60, or 1440.

    Note from manual testing: a wide range at 60-minute resolution did
    NOT return one candle per hour — only candles for periods with
    actual data came back. Don't assume dense coverage; always check
    len(candlesticks) before assuming a specific index/offset is safe,
    and prefer a narrow window (see pick_pregame_candle) over pulling a
    market's whole lifetime.
    """
    params = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "period_interval": period_interval,
        "include_latest_before_start": str(include_latest_before_start).lower(),
    }
    payload = _get(f"/series/{series_ticker}/markets/{ticker}/candlesticks", params)
    return payload.get("candlesticks", [])


def pick_pregame_candle(
    series_ticker: str,
    ticker: str,
    scheduled_start_utc: datetime,
    window_minutes_before: int = 30,
) -> Optional[dict]:
    """The main entry point for the historical snapshot pipeline: given a
    game's AUTHORITATIVE scheduled start time (from schedule.py, not from
    the ticker), pulls a narrow 1-minute-resolution window ending at
    kickoff and returns the single candle closest to (but not after) it.

    Returns None if no candlestick data exists in the window (illiquid
    market, or the market didn't exist yet at that time — flag these for
    manual review rather than silently dropping the game).
    """
    if scheduled_start_utc.tzinfo is None:
        scheduled_start_utc = scheduled_start_utc.replace(tzinfo=timezone.utc)

    target_ts = int(scheduled_start_utc.timestamp())
    start_ts = target_ts - window_minutes_before * 60
    end_ts = target_ts  # candlesticks endpoint includes candles ending on/before end_ts

    candles = get_candlesticks(
        series_ticker, ticker, start_ts, end_ts,
        period_interval=1, include_latest_before_start=True,
    )
    if not candles:
        return None

    # Closest end_period_ts at or before kickoff. include_latest_before_start
    # means even an illiquid window should return at least one synthetic
    # candle carried forward from the last real trade.
    candles_before = [c for c in candles if c["end_period_ts"] <= target_ts]
    pool = candles_before or candles
    return min(pool, key=lambda c: abs(c["end_period_ts"] - target_ts))


def markets_for_date_range(
    series_ticker: str, team_map_file: str, start_date: date, end_date: date
) -> list[dict]:
    """Convenience wrapper: all settled markets in a series whose ticker
    date falls within [start_date, end_date]. Filters client-side after
    list_settled_markets() rather than relying on a date param, since the
    /markets endpoint doesn't document one that's reliably inclusive —
    verify this assumption cheaply against a known week before trusting
    it for a full-season pull.

    team_map_file is whichever sport's lookup table applies (e.g.
    "nfl_teams.json") — this function is sport-agnostic as long as the
    caller passes the matching series_ticker/team_map_file pair from
    config.yaml.
    """
    from src.matching.event_matcher import parse_kalshi_ticker

    all_markets = list_settled_markets(series_ticker)
    out = []
    for m in all_markets:
        parsed = parse_kalshi_ticker(m["ticker"], team_map_file=team_map_file)
        if parsed and start_date <= parsed["event_date"] <= end_date:
            out.append(m)
    return out
