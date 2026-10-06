# CLAUDE GENERATED CODE TO EXTRACT HISTORICAL CLOSING LINES FOR GIVEN SPORT AND TIME INTERVAL

"""
sportsbook_history.py

Wraps OddsHarvester (https://github.com/jordantete/OddsHarvester) for
historical closing lines, and normalizes its output into the same shape
schedule.py and event_matcher.py already use (home_team/away_team/
start_time), so the join step doesn't need to know which fetcher a row
came from. Config-driven per sport (see config.yaml's oddsharvester_*
keys) — nothing in this file is NFL-specific.

Install separately (not in requirements.txt — it pulls in Playwright,
which is a heavier dependency you may not want for the rest of the
project):
    pip install oddsharvester
    playwright install chromium
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

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

def decimal_to_american(decimal_odds: float) -> int:
    """Converts a decimal odds quote (e.g. 1.50) to American odds
    (e.g. -200). Pure notation conversion, not a probability calculation
    — see devig.py for turning either format into an implied probability."""
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def run_historic_scrape(
    sport: str,
    league: str,
    market: str,
    season: str,
    output_path: str,
    bookmaker_filter: str | None = None,
    headless: bool = True,
    concurrency: int = 3,
    checkpoint_path: str | None = None,
    max_pages: int | None = None,
) -> Path:
    """Shells out to the oddsharvester CLI for one sport/league/market/season.

    ADDED 2026-09-23: uses --stream-ndjson so oddsharvester emits one JSON
    line per match to stdout AS SOON as it's scraped, rather than only
    writing the final --output file after the entire run finishes. When
    checkpoint_path is given, every streamed match is appended and flushed
    to that file immediately, so a hard interruption (forced OS restart,
    power loss, anything that kills the process outright) leaves real,
    readable progress on disk instead of nothing. The normal --output file
    still gets written at the end on a successful run, so load_and_normalize()
    is unaffected on the happy path — checkpoint_path is purely a safety net
    for the unhappy one. See recover_from_checkpoint() below.

    FIXED 2026-09-23 (source-level pass, done separately from the
    concurrency/checkpoint work above and merged back in): removed a stray
    duplicate copy of this function's body that had ended up living below
    the real `return` — genuinely unreachable, never executed, but worth
    knowing it was there since it looked like the checkpoint logic was
    duplicated. Also, `bookmaker_filter` was accepted as a parameter here
    but never wired into `cmd` — a no-op. It's now passed through as
    `--target-bookmaker` (confirmed real flag, cli/options.py). Also added
    `max_pages` -> `--max-pages`: date_range in config.yaml does NOT limit
    this step (it's driven entirely by `season`), so it's still the only
    real lever for a cheap capped run on a sport you haven't scraped yet —
    see build_historical_dataset.py's docstring.

    Writes raw JSON to output_path and returns its Path. Does not parse
    it — call load_and_normalize() separately so a bad parse doesn't
    force a re-scrape.
    """
    cmd = [
        "oddsharvester", "historic",
        "-s", sport,
        "-l", league,
        "--season", season,
        "-m", market,
        "-f", "json",
        "-o", output_path,
        "-c", str(concurrency),
        "--stream-ndjson",
    ]
    if headless:
        cmd.append("--headless")
    if bookmaker_filter:
        cmd.extend(["--target-bookmaker", bookmaker_filter])
    if max_pages is not None:
        cmd.extend(["--max-pages", str(max_pages)])

    print(f"Running: {' '.join(cmd)}")
    scrape_start = time.monotonic()

    checkpoint_file = None
    checkpoint_count = 0
    if checkpoint_path:
        checkpoint_file = open(checkpoint_path, "a", encoding="utf-8")
        print(f"  Checkpointing each match to {checkpoint_path} as it's scraped")

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output_lines = []
    try:
        for line in process.stdout:
            stripped = line.rstrip()
            is_match_record = False
            if checkpoint_file and stripped.startswith("{"):
                try:
                    json.loads(stripped)
                    is_match_record = True
                except json.JSONDecodeError:
                    pass

            if is_match_record:
                checkpoint_file.write(stripped + "\n")
                checkpoint_file.flush()
                checkpoint_count += 1
                if checkpoint_count % 25 == 0:
                    elapsed = time.monotonic() - scrape_start
                    rate = checkpoint_count / (elapsed / 60) if elapsed > 0 else 0
                    print(f"  [checkpoint] {checkpoint_count} matches saved "
                          f"({_fmt_duration(elapsed)} elapsed, ~{rate:.1f} matches/min)")
            else:
                print(f"  [oddsharvester] {stripped}")
            output_lines.append(line)
    finally:
        if checkpoint_file:
            checkpoint_file.close()

    process.wait()
    total_elapsed = time.monotonic() - scrape_start

    if process.returncode != 0:
        raise RuntimeError(
            f"oddsharvester exited {process.returncode} after {_fmt_duration(total_elapsed)} "
            f"({checkpoint_count} matches were checkpointed to {checkpoint_path} before it died)"
            f"\nlast output:\n{''.join(output_lines[-40:])}"
        )
    print(f"  Scrape finished in {_fmt_duration(total_elapsed)} ({checkpoint_count} matches checkpointed)")
    return Path(output_path if output_path.endswith(".json") else f"{output_path}.json")


def recover_from_checkpoint(checkpoint_path: str, recovered_output_path: str) -> Path:
    """Turns a partial NDJSON checkpoint (from a run that never finished —
    OS restart, power loss, crash) into a plain JSON array in the same
    shape load_and_normalize() already expects, so an interrupted overnight
    run doesn't mean starting over from zero.

    Only meant for after an interrupted run. On a normal successful run,
    use the real --output file instead — it's oddsharvester's own complete,
    de-duplicated result, not a reconstruction.
    """
    matches = []
    with open(checkpoint_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            matches.append(json.loads(line))

    Path(recovered_output_path).write_text(json.dumps(matches, indent=2))
    print(f"Recovered {len(matches)} matches from {checkpoint_path} -> {recovered_output_path}")
    return Path(recovered_output_path)

def _extract_moneyline_prices(odds_entries: list[dict], preferred_bookmaker: str | None) -> dict | None:
    """Pulls (home_decimal, away_decimal) out of one match's `odds` list.
    Tries the preferred bookmaker first, falls back to the first entry
    that has usable prices.

    Defensive about key names since this hasn't been verified against a
    real American-football response yet (see module docstring). A
    2-outcome market might come back as "1"/"2", "home"/"away", or
    something else entirely — if this raises, print(entry) on a real
    sample and adjust the key list below.
    """
    candidates = odds_entries
    if preferred_bookmaker:
        pref_lower = preferred_bookmaker.lower()
        preferred = [
            e for e in odds_entries
            if pref_lower in (e.get("bookmaker_name") or "").lower()
        ]
        candidates = preferred or odds_entries

    home_keys = ("1", "home", "Home")
    away_keys = ("2", "away", "Away")

    for entry in candidates:
        home_val = next((entry[k] for k in home_keys if k in entry), None)
        away_val = next((entry[k] for k in away_keys if k in entry), None)
        if home_val is not None and away_val is not None:
            return {
                "bookmaker": entry.get("bookmaker_name"),
                "home_decimal": float(home_val),
                "away_decimal": float(away_val),
            }

    if odds_entries:
        raise KeyError(
            f"Couldn't find home/away price keys in odds entry — got keys "
            f"{list(odds_entries[0].keys())}. Update home_keys/away_keys above."
        )
    return None


def load_and_normalize(json_path: str, preferred_bookmaker: str | None = "Bet365") -> list[dict]:
    """Reads an OddsHarvester JSON output file and normalizes each match
    into: home_team, away_team, start_time_utc, bookmaker,
    home_odds_american, away_odds_american, home_score, away_score.

    Team names come straight from oddsportal via OddsHarvester — these
    will almost certainly NOT match ESPN's team.displayName strings
    exactly (e.g. possible "Man Utd" vs "Manchester United" style
    differences, though NFL team names are less abbreviation-prone than
    soccer club names). Build and run a verify_team_names.py-style check
    (see scripts/) before trusting the join at scale, same as the
    original MLB matcher did for TheOddsAPI vs Kalshi team names.
    """
    raw = json.loads(Path(json_path).read_text())
    matches = raw if isinstance(raw, list) else raw.get("data", raw)

    normalized = []
    for match in matches:
        prices = _extract_moneyline_prices(match.get("home_away_market", []), preferred_bookmaker)
        if prices is None:
            continue

        start_time_utc = match["match_date"]
        if not start_time_utc.endswith("Z") and "+" not in start_time_utc:
            start_time_utc = start_time_utc.replace(" UTC", "").replace(" ", "T") + "Z"

        normalized.append({
            "home_team": match["home_team"],
            "away_team": match["away_team"],
            "start_time_utc": start_time_utc,
            "bookmaker": prices["bookmaker"],
            "home_odds_american": decimal_to_american(prices["home_decimal"]),
            "away_odds_american": decimal_to_american(prices["away_decimal"]),
            "home_score": int(match["home_score"]) if match.get("home_score") not in (None, "") else None,
            "away_score": int(match["away_score"]) if match.get("away_score") not in (None, "") else None,
        })
    return normalized
