"""
build_historical_dataset.py — main entry point. Reads config/config.yaml,
runs the full pull-match-join-write pipeline for `active_sport` over
`date_range`, and writes the result to `output_csv`.

This is the replacement for the old scheduler.py + snapshot.py +
settlement.py trio: since this is a historical backfill, the result of
every game is already known at pull time, so there's no separate
"settle later" step — everything happens in one pass.

Run locally (this will NOT work inside the Claude cloud session — see
the network-access note from earlier in the chat):

    pip install -r requirements.txt
    pip install oddsharvester && playwright install chromium
    python scripts/build_historical_dataset.py

First run recommendation (the pilot checkpoint): edit config.yaml's
date_range to a single week before running the full season, and
eyeball the printed sample rows — especially whether kalshi_* and
sportsbook_* columns are actually populated, not just schedule/score
columns. An all-null Kalshi or sportsbook column means a matching step
is silently failing and is much cheaper to catch on a week of data than
after a full-season run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from src.fetchers import kalshi_history, schedule, sportsbook_history
from src.pipeline.historical_snapshot import build_dataset
from src.storage.csv_writer import write_rows

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def main():
    config = yaml.safe_load(CONFIG_PATH.read_text())

    sport_key = config["active_sport"]
    sport_cfg = config["sports"][sport_key]
    start_date = datetime.strptime(config["date_range"]["start"], "%Y-%m-%d").date()
    end_date = datetime.strptime(config["date_range"]["end"], "%Y-%m-%d").date()

    print(f"=== Building historical dataset: {sport_key} {start_date} to {end_date} ===\n")

    print("[1/4] Pulling schedule + results from ESPN...")
    schedule_games = schedule.get_schedule(sport_cfg["espn_sport_key"], start_date, end_date)
    print(f"      {len(schedule_games)} games found\n")

    print("[2/4] Pulling Kalshi settled markets for the same window...")
    kalshi_markets = kalshi_history.markets_for_date_range(
        sport_cfg["kalshi_series"], sport_cfg["team_map_file"], start_date, end_date
    )
    print(f"      {len(kalshi_markets)} Kalshi market entries found "
          f"(2 per game if both sides settled)\n")

    print("[3/4] Sportsbook closing lines...")
    scrape_output = f"data/raw_oddsharvester_{sport_key}.json"
    if Path(scrape_output).exists():
        print(f"      Found existing {scrape_output}, skipping re-scrape "
              f"(delete it to force a fresh pull)")
    else:
        season_str = sport_cfg["oddsharvester_season"]
        sportsbook_history.run_historic_scrape(
            sport_cfg["oddsharvester_sport"], sport_cfg["oddsharvester_league"],
            sport_cfg["oddsharvester_market"], season_str, scrape_output,
            concurrency=sport_cfg.get("oddsharvester_concurrency", 3),
        )
    sportsbook_rows = sportsbook_history.load_and_normalize(
        scrape_output, preferred_bookmaker=config["sportsbook_bookmaker"]
    )
    print(f"      {len(sportsbook_rows)} games with usable odds\n")

    print("[4/4] Joining and writing CSV...")
    rows = build_dataset(
        team_map_file=sport_cfg["team_map_file"],
        schedule_games=schedule_games,
        kalshi_markets=kalshi_markets,
        sportsbook_rows=sportsbook_rows,
        candle_window_minutes=config["candle_window_minutes_before_kickoff"],
    )
    write_rows(rows, config["output_csv"])

    complete = sum(
        1 for r in rows
        if r["kalshi_home_close_price"] is not None and r["sportsbook_home_odds_american"] is not None
    )
    print(f"\n{complete}/{len(rows)} rows have BOTH a Kalshi price and a sportsbook line "
          f"— that's your usable sample size for analysis.")


if __name__ == "__main__":
    main()
