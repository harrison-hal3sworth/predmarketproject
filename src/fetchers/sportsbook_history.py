"""
sportsbook_history.py

Wraps OddsHarvester (https://github.com/jordantete/OddsHarvester) for
NFL historical closing lines, and normalizes its output into the same
shape schedule.py and event_matcher.py already use (home_team/away_team/
start_time), so the join step doesn't need to know which fetcher a row
came from.

STATUS: written from OddsHarvester's README, not yet verified against
a real scrape — unlike kalshi_history.py and schedule.py, which are
built against confirmed live responses. This is exactly the "pilot"
checkpoint from earlier: run scrape_season() for one week of the 2025
season first and check the printed sample rows before trusting this at
full-season scale. The most likely thing to need adjusting is the exact
key names inside each `odds` entry (`_extract_moneyline_prices` below)
once you see a real American-football response — soccer's 1X2 market
(the documented example) has a draw outcome NFL doesn't, so the key
names may differ.

Install separately (not in requirements.txt — it pulls in Playwright,
which is a heavier dependency you may not want for the rest of the
project):
    pip install oddsharvester
    playwright install chromium

The only unit conversion done here is decimal odds -> American odds —
a pure notation conversion (both represent the exact same raw quoted
price), needed because the rest of this codebase's math (devig.py)
already expects American odds. No probability or de-vig math happens
in this file.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path


def decimal_to_american(decimal_odds: float) -> int:
    """Converts a decimal odds quote (e.g. 1.50) to American odds
    (e.g. -200). Pure notation conversion, not a probability calculation
    — see devig.py for turning either format into an implied probability."""
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def run_historic_scrape(
    season: str,
    output_path: str,
    bookmaker_filter: str | None = None,
    headless: bool = True,
) -> Path:
    """Shells out to the oddsharvester CLI for NFL moneyline odds for one
    season (e.g. "2025" or "2025-2026" — confirm which format the
    installed version expects; the README examples were inconsistent
    between single-year and hyphenated forms for different sports).

    Writes raw JSON to output_path and returns its Path. Does not parse
    it — call load_and_normalize() separately so a bad parse doesn't
    force a re-scrape.
    """
    cmd = [
        "oddsharvester", "historic",
        "-s", "american-football",
        "-l", "usa-nfl",
        "--season", season,
        "-m", "moneyline",
        "-f", "json",
        "-o", output_path,
    ]
    if headless:
        cmd.append("--headless")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"oddsharvester exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return Path(output_path if output_path.endswith(".json") else f"{output_path}.json")


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
        preferred = [e for e in odds_entries if e.get("bookmaker_name") == preferred_bookmaker]
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
        prices = _extract_moneyline_prices(match.get("odds", []), preferred_bookmaker)
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
