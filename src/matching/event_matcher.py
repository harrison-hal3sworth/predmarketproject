# CLAUDE GENERATED CODE TO MATCH KALSHI EVENTS TO THE ODDS API EVENTS

"""
event_matcher.py

Matches a Kalshi MLB market to the corresponding TheOddsAPI game for the
same real-world matchup, using the team code -> full name mapping in
data/lookup_tables/mlb_teams.json.

Kalshi ticker shape: KXMLBGAME-{YY}{MON}{DD}{HHMM}{AWAYCODE}{HOMECODE}-{SIDE}
e.g. KXMLBGAME-26SEP152140MIAAZ-MIA
"""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

MAPPING_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "lookup_tables" / "mlb_teams.json"
_TEAM_MAP = None
EASTERN = ZoneInfo("America/New_York")

def load_team_map() -> dict:
    global _TEAM_MAP
    if _TEAM_MAP is None:
        _TEAM_MAP = json.loads(MAPPING_FILE.read_text())
    return _TEAM_MAP


TICKER_RE = re.compile(r"^KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)-([A-Z]+)$")


def parse_kalshi_ticker(ticker: str) -> dict | None:
    """Parses a Kalshi MLB ticker into its components, or None if it doesn't match."""
    match = TICKER_RE.match(ticker)
    if not match:
        return None

    yy, mon, dd, hhmm, teams_blob, side_code = match.groups()
    team_map = load_team_map()
    away_code, home_code = _split_matchup(teams_blob, set(team_map.keys()))
    if away_code is None:
        return None

    naive_dt = datetime(
        2000 + int(yy), datetime.strptime(mon, "%b").month, int(dd),
        int(hhmm[:2]), int(hhmm[2:]),
    )
    start_time = naive_dt.replace(tzinfo=EASTERN).astimezone(timezone.utc)

    return {
        "ticker": ticker,
        "start_time": start_time,
        "away_team": team_map.get(away_code),
        "home_team": team_map.get(home_code),
        "side_team": team_map.get(side_code),
    }


def _split_matchup(blob: str, known_codes: set):
    """Splits a concatenated away+home code blob (e.g. 'MIAAZ') by testing
    every split point against the known code set."""
    for i in range(1, len(blob)):
        left, right = blob[:i], blob[i:]
        if left in known_codes and right in known_codes:
            return left, right
    return None, None


def match_to_odds_game(kalshi_parsed: dict, odds_games: list, tolerance_minutes: int = 20):
    """Finds the TheOddsAPI game matching a parsed Kalshi market, by team
    pair + start-time proximity (handles doubleheaders via the time check)."""
    if kalshi_parsed is None:
        return None

    kalshi_teams = {kalshi_parsed["away_team"], kalshi_parsed["home_team"]}
    best_match, best_diff = None, None

    for game in odds_games:
        if {game["home_team"], game["away_team"]} != kalshi_teams:
            continue
        odds_start = datetime.fromisoformat(game["start_time"].replace("Z", "+00:00"))
        diff = abs((odds_start - kalshi_parsed["start_time"]).total_seconds()) / 60
        if diff <= tolerance_minutes and (best_diff is None or diff < best_diff):
            best_match, best_diff = game, diff

    return best_match