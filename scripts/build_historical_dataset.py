# CLAUDE GENERATED CODE TO CONNECT, PULL AND FORM DATASET FOR GIVEN SPORT AND TIMEFRAME

"""
build_historical_dataset.py — main entry point. Reads config/config.yaml,
runs the full pull-match-join-write pipeline for `active_sport` over
`date_range`, and writes the result to `output_csv`.

***DO FOR FIRST RUN AS PROOF OF CONCEPT***
First run recommendation (the pilot checkpoint): edit config.yaml's
date_range to a single week before running the full season, and
eyeball the printed sample rows — especially whether kalshi_* and
sportsbook_* columns are actually populated, not just schedule/score
columns. An all-null Kalshi or sportsbook column means a matching step
is silently failing and is much cheaper to catch on a week of data than
after a full-season run.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.fetchers import kalshi_history, schedule, sportsbook_history
from src.pipeline.historical_snapshot import build_dataset
from src.storage.csv_writer import write_rows

def _fmt_duration(seconds: float) -> str:
    """Human-readable elapsed time, e.g. '2h 14m 03s' or '47s'."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def main():
    run_start = time.monotonic()
    config = yaml.safe_load(CONFIG_PATH.read_text())

    sport_key = config["active_sport"]
    sport_cfg = config["sports"][sport_key]
    start_date = datetime.strptime(config["date_range"]["start"], "%Y-%m-%d").date()
    end_date = datetime.strptime(config["date_range"]["end"], "%Y-%m-%d").date()

    print(f"=== Building historical dataset: {sport_key} {start_date} to {end_date} ===\n")

    step_start = time.monotonic()
    print("[1/4] Pulling schedule + results from ESPN...")
    schedule_games = schedule.get_schedule(sport_cfg["espn_sport_key"], start_date, end_date)
    print(f"      {len(schedule_games)} games found ({_fmt_duration(time.monotonic() - step_start)})\n")

    step_start = time.monotonic()
    print("[2/4] Pulling Kalshi settled markets for the same window...")
    kalshi_markets = kalshi_history.markets_for_date_range(
        sport_cfg["kalshi_series"], sport_cfg["team_map_file"], start_date, end_date
    )
    print(f"      {len(kalshi_markets)} Kalshi market entries found "
          f"(2 per game if both sides settled) ({_fmt_duration(time.monotonic() - step_start)})\n")

    step_start = time.monotonic()
    print("[3/4] Sportsbook closing lines...")
    scrape_output = f"data/raw_oddsharvester_{sport_key}.json"
    complete_marker = Path(f"{scrape_output}.complete")
    if complete_marker.exists():
        print(f"      Found completed {scrape_output}, skipping re-scrape "
              f"(delete {complete_marker.name} in data/, or the raw file itself, "
              f"to force a fresh pull)")
    else:
        season_str = sport_cfg["oddsharvester_season"]
        checkpoint_path = f"data/raw_oddsharvester_{sport_key}.checkpoint.ndjson"
        pilot_max_pages = sport_cfg.get("oddsharvester_pilot_max_pages")
        if pilot_max_pages:
            print(f"      oddsharvester_pilot_max_pages={pilot_max_pages} set — "
                  f"this is a capped pilot scrape, not the full season\n")
        sportsbook_history.run_historic_scrape(
            sport_cfg["oddsharvester_sport"], sport_cfg["oddsharvester_league"],
            sport_cfg["oddsharvester_market"], season_str, scrape_output,
            concurrency=sport_cfg.get("oddsharvester_concurrency", 3),
            checkpoint_path=checkpoint_path,
            max_pages=pilot_max_pages,
        )
        # Only reached if run_historic_scrape() returned without raising, i.e.
        # oddsharvester exited 0 — a genuinely complete run, not a partial one
        # recovered from the checkpoint.
        complete_marker.touch()
    sportsbook_rows = sportsbook_history.load_and_normalize(
        scrape_output, preferred_bookmaker=config["sportsbook_bookmaker"]
    )
    print(f"      {len(sportsbook_rows)} games with usable odds ({_fmt_duration(time.monotonic() - step_start)})\n")

    step_start = time.monotonic()
    print("[4/4] Joining and writing CSV...")
    rows = build_dataset(
        team_map_file=sport_cfg["team_map_file"],
        schedule_games=schedule_games,
        kalshi_markets=kalshi_markets,
        sportsbook_rows=sportsbook_rows,
        candle_window_minutes=config["candle_window_minutes_before_kickoff"],
    )
    write_rows(rows, config["output_csv"])
    print(f"      done ({_fmt_duration(time.monotonic() - step_start)})")

    complete = sum(
        1 for r in rows
        if r["kalshi_home_close_price"] is not None and r["sportsbook_home_odds_american"] is not None
    )
    print(f"\n{complete}/{len(rows)} rows have BOTH a Kalshi price and a sportsbook line "
          f"— that's your usable sample size for analysis.")
    print(f"\n=== Total runtime: {_fmt_duration(time.monotonic() - run_start)} ===")

if __name__ == "__main__":
    main()
