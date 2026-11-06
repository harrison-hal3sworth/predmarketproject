# Contains Claude generated code

"""
fetch_kalshi_early_prices.py

Pulls earlier Kalshi quotes (T-24h, T-6h, T-2h before kickoff) for every
game already in the dataset, for the weak-form efficiency test (models
notebook, section 4). Uses the Kalshi tickers already stored in
data/{sport}_2025_historical.csv, so nothing else in the pipeline reruns:
no schedule pull, no market listing, no matching, no sportsbook scrape.

Uses 1-minute candles, the same resolution the main pipeline uses (the first
version asked for hourly candles, and Kalshi's historical endpoint returned
none for every market). One request per ticker covers the 25 hours before
kickoff; a short targeted request is added only for a horizon that request
didn't reach. About 9,700+ requests through 4 worker threads, roughly
20-30 minutes.

Run locally from the project root (Kalshi is blocked from the Claude sandbox):
    python scripts/fetch_kalshi_early_prices.py --probe     # 5 tickers, ~10 s, prints raw counts
    python scripts/fetch_kalshi_early_prices.py             # full run
    python analysis/build_model_table.py                    # merges the new columns

The full run probes the first 20 tickers itself and stops if none of them
returns a quote, rather than writing thousands of empty results.

Writes data/kalshi_early_prices.csv, one row per game x side x horizon:
    game_id, side, ticker, hours_before, target_ts, candle_end_ts,
    stale_hours, bid, ask, price, volume, open_interest

For each horizon the quote is taken from the last 1-minute candle ending at
or before kickoff minus that many hours (`stale_hours` records the actual
lag; it is larger for markets with no activity around then). Missing rows mean Kalshi had no candle
at all by then, e.g. the market had not opened.

Resumable: each finished ticker is appended to
data/kalshi_early_prices.v2.checkpoint.ndjson and skipped on a rerun, so an
interruption loses at most the in-flight requests. (The v1 checkpoint from
the hourly-candle attempt holds only empty results and is ignored.)
"""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.fetchers import kalshi_history  # noqa: E402

SPORTS = ["nfl", "mlb", "nba", "nhl"]
SEASON = 2025
HORIZONS_H = [24, 6, 2]
WORKERS = 4
OUT_PATH = ROOT / "data" / "kalshi_early_prices.csv"
CHECKPOINT = ROOT / "data" / "kalshi_early_prices.v2.checkpoint.ndjson"
WINDOW_H = max(HORIZONS_H) + 1    # hours of 1-minute candles in the main request
FALLBACK_MIN = 60                 # minutes before T-h covered by a targeted request
STALE_REFETCH_H = 0.25            # refetch a horizon whose quote is older than this...


def _f(x):
    return float(x) if x not in (None, "") else None


def _close(candle: dict, field: str):
    block = candle.get(field)
    if isinstance(block, dict):
        return _f(block.get("close", block.get("close_dollars")))
    return _f(candle.get(f"{field}_close"))


def pick_quotes(candles: list[dict], kickoff_ts: int) -> list[dict]:
    """For each horizon, the last candle ending at or before kickoff - h hours."""
    out = []
    for h in HORIZONS_H:
        target = kickoff_ts - h * 3600
        before = [c for c in candles if c.get("end_period_ts") is not None and c["end_period_ts"] <= target]
        if not before:
            continue
        c = max(before, key=lambda c: c["end_period_ts"])
        out.append({
            "hours_before": h, "target_ts": target, "candle_end_ts": c["end_period_ts"],
            "stale_hours": round((target - c["end_period_ts"]) / 3600, 2),
            "bid": _close(c, "yes_bid"), "ask": _close(c, "yes_ask"), "price": _close(c, "price"),
            "volume": _f(c.get("volume", c.get("volume_fp"))),
            "open_interest": _f(c.get("open_interest", c.get("open_interest_fp"))),
        })
    return out


def _candles(ticker: str, start_ts: int, end_ts: int) -> list[dict]:
    return kalshi_history.get_candlesticks(
        ticker, start_ts, end_ts, period_interval=1, include_latest_before_start=True)


def fetch_one(ticker: str, kickoff_ts: int) -> list[dict]:
    """One 25-hour request of 1-minute candles, then a targeted request for any
    horizon it missed or (if the response may have been truncated) only reached
    with a stale quote."""
    candles = _candles(ticker, kickoff_ts - WINDOW_H * 3600, kickoff_ts)
    latest = max((c["end_period_ts"] for c in candles if c.get("end_period_ts") is not None), default=None)
    quotes = {q["hours_before"]: q for q in pick_quotes(candles, kickoff_ts)}

    for h in HORIZONS_H:
        target = kickoff_ts - h * 3600
        q = quotes.get(h)
        maybe_truncated = latest is None or latest < target
        if q is None or (q["stale_hours"] > STALE_REFETCH_H and maybe_truncated):
            extra = _candles(ticker, target - FALLBACK_MIN * 60, target)
            hit = [x for x in pick_quotes(extra, kickoff_ts) if x["hours_before"] == h]
            if hit and (q is None or hit[0]["stale_hours"] < q["stale_hours"]):
                quotes[h] = hit[0]
    return [quotes[h] for h in HORIZONS_H if h in quotes]


def probe(jobs: pd.DataFrame, n: int) -> int:
    """Fetch n tickers without saving, print what came back. Returns how many had a quote."""
    sample = jobs.groupby(jobs.index % max(1, len(jobs) // n)).head(1).head(n) if len(jobs) > n else jobs
    hits = 0
    for r in sample.itertuples():
        raw = _candles(r.ticker, int(r.kickoff_ts) - WINDOW_H * 3600, int(r.kickoff_ts))
        quotes = fetch_one(r.ticker, int(r.kickoff_ts))
        hits += bool(quotes)
        print(f"  {r.ticker}: {len(raw)} raw 1-min candles, quotes at "
              f"{[q['hours_before'] for q in quotes]} h  (stale h: {[q['stale_hours'] for q in quotes]})")
        if raw and r.Index == sample.index[0]:
            print("    first candle keys:", sorted(raw[0].keys()))
    return hits


def jobs_from_raw() -> pd.DataFrame:
    rows = []
    for sport in SPORTS:
        d = pd.read_csv(ROOT / "data" / f"{sport}_{SEASON}_historical.csv",
                        usecols=["game_id", "start_time_utc", "kalshi_home_ticker", "kalshi_away_ticker"],
                        dtype={"game_id": str})
        # Resolution-independent: pandas 3 parses to microseconds, pandas 2 to nanoseconds,
        # so astype("int64") // 10**9 is only right on pandas 2.
        kick = (pd.to_datetime(d["start_time_utc"], utc=True)
                - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
        for side in ["home", "away"]:
            rows.append(pd.DataFrame({"game_id": d["game_id"], "side": side,
                                      "ticker": d[f"kalshi_{side}_ticker"], "kickoff_ts": kick}))
    jobs = pd.concat(rows, ignore_index=True).dropna(subset=["ticker"])
    assert jobs["kickoff_ts"].between(1.7e9, 1.8e9).all(), "kickoff timestamps aren't 2025-26 Unix seconds"

    # Only games that made it into the modeling table, when it exists.
    mt = ROOT / "data" / "model_table.csv"
    if mt.exists():
        keep = set(pd.read_csv(mt, usecols=["game_id"], dtype={"game_id": str})["game_id"])
        jobs = jobs[jobs["game_id"].isin(keep)]
    return jobs.reset_index(drop=True)


def main():
    jobs = jobs_from_raw()
    if "--probe" in sys.argv:
        hits = probe(jobs, 5)
        print(f"\n{hits} of 5 probed tickers returned a quote.")
        return
    done: dict[str, list[dict]] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["ticker"]] = rec["quotes"]

    todo = jobs[~jobs["ticker"].isin(done)]
    print(f"{len(jobs)} tickers ({len(jobs) // 2} games); {len(done)} already fetched, {len(todo)} to go")
    if len(todo) and not any(done.values()):
        print("Checking 20 tickers first...")
        if probe(todo, 20) == 0:
            sys.exit("None of 20 probed tickers returned a quote, so stopping before the full run. "
                     "Send the output above to Claude.")

    lock = threading.Lock()
    failures = []
    t0 = time.monotonic()
    with open(CHECKPOINT, "a", encoding="utf-8") as ckpt, ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(fetch_one, r.ticker, int(r.kickoff_ts)): r.ticker for r in todo.itertuples()}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker = futures[fut]
            try:
                quotes = fut.result()
            except Exception as exc:  # keep going; a rerun retries anything that failed
                failures.append((ticker, str(exc)[:200]))
                continue
            with lock:
                done[ticker] = quotes
                ckpt.write(json.dumps({"ticker": ticker, "quotes": quotes}) + "\n")
                ckpt.flush()
            if i % 250 == 0:
                rate = i / max(time.monotonic() - t0, 1e-9)
                print(f"  {i}/{len(todo)} tickers  ({rate:.1f}/s, ~{(len(todo) - i) / rate / 60:.0f} min left)")

    rows = [{"game_id": r.game_id, "side": r.side, "ticker": r.ticker, **q}
            for r in jobs.itertuples() for q in done.get(r.ticker, [])]
    out = pd.DataFrame(rows)
    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH.relative_to(ROOT)} ({len(out)} rows)")

    if failures:
        print(f"\n{len(failures)} tickers failed (rerun the script to retry them). First few:")
        for t, e in failures[:5]:
            print(f"  {t}: {e}")
    if len(out):
        quoted = out[(out["bid"] > 0) & (out["ask"] < 1)]
        cov = quoted.groupby("hours_before")["ticker"].nunique() / len(jobs)
        print("\nShare of tickers with a two-sided quote, by horizon:")
        print(cov.round(3).to_string())
        print("\nMedian staleness (hours) by horizon:")
        print(out.groupby("hours_before")["stale_hours"].median().to_string())


if __name__ == "__main__":
    main()
