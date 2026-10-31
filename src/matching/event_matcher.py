# Contains Claude generated code

"""
event_matcher.py

Parses a Kalshi game-winner ticker for ANY sport series (config-driven, not hardcoded)
and matches it to a row from our own schedule data (src/fetchers/schedule.py),
not to a live odds API response — this pipeline is a historical backfill,
so "the schedule" is the ground truth for team names and kickoff time.

Important design note carried over from the original MLB matcher, made
explicit here because it matters more once sports without an embedded
time are involved: Kalshi's ticker for some sports (MLB) embeds an
HHMM block, used historically to disambiguate doubleheaders. Others
(NFL, confirmed against live data on 2026-09-22) do NOT embed a time —
the ticker is just date + teams. Either way, the AUTHORITATIVE scheduled
start time for candle alignment should come from the schedule pull
(ESPN), never from the ticker. The embedded time (when present) is only
used here as a secondary disambiguator when multiple games between the
same two teams happen on the same date (doubleheaders).

Kalshi ticker examples:
  MLB: KXMLBGAME-{YY}{MON}{DD}{HHMM}{AWAYCODE}{HOMECODE}-{SIDE}
       e.g. KXMLBGAME-26SEP152140MIAAZ-MIA
  NFL: KXNFLGAME-{YY}{MON}{DD}{AWAYCODE}{HOMECODE}-{SIDE}
       e.g. KXNFLGAME-26SEP21NYGLAR-LAR   (no HHMM block)
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "lookup_tables"
EASTERN = ZoneInfo("America/New_York")

# Matches an HHMM block only when it's immediately followed by two runs of
# uppercase letters and then the ticker ends after a trailing "-SIDE".
# We try this pattern first; if the whole string doesn't have a plausible
# 4-digit time block, we fall back to the no-time pattern.
_TICKER_WITH_TIME_RE = re.compile(
    r"^(?P<series>[A-Z]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<hhmm>\d{4})(?P<teams>[A-Z]+)-(?P<side>[A-Z]+)$"
)
_TICKER_NO_TIME_RE = re.compile(
    r"^(?P<series>[A-Z]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<teams>[A-Z]+)-(?P<side>[A-Z]+)$"
)

_team_map_cache: dict[str, dict] = {}


def load_team_map(team_map_file: str) -> dict:
    """Loads (and caches) a sport's code -> full-name lookup table, e.g.
    'nfl_teams.json' or 'mlb_teams.json' (relative to data/lookup_tables/)."""
    if team_map_file not in _team_map_cache:
        path = DATA_DIR / team_map_file
        _team_map_cache[team_map_file] = json.loads(path.read_text())
    return _team_map_cache[team_map_file]


def _split_matchup(blob: str, known_codes: set) -> tuple[Optional[str], Optional[str]]:
    """Splits a concatenated away+home code blob (e.g. 'NYGLAR') by testing
    every split point against the known code set. Unchanged from the
    original MLB matcher — this part was already sport-agnostic."""
    for i in range(1, len(blob)):
        left, right = blob[:i], blob[i:]
        if left in known_codes and right in known_codes:
            return left, right
    return None, None


def parse_kalshi_ticker(ticker: str, team_map_file: str) -> Optional[dict]:
    """Parses a Kalshi game-winner ticker into its components, trying the
    with-time pattern first and falling back to the no-time pattern.
    Returns None if neither matches or the team codes can't be split.

    Returned dict:
      ticker, series, event_date (date, from the ticker itself — NOT
      authoritative kickoff time), embedded_time (datetime.time or None),
      away_code, home_code, away_team, home_team, side_code, side_team
    """
    team_map = load_team_map(team_map_file)
    known_codes = set(team_map.keys())

    embedded_time = None
    away_code = home_code = None

    match = _TICKER_WITH_TIME_RE.match(ticker)
    if match:
        groups = match.groupdict()
        away_code, home_code = _split_matchup(groups["teams"], known_codes)
        if away_code is not None:
            hhmm = groups["hhmm"]
            embedded_time = (int(hhmm[:2]), int(hhmm[2:]))
        else:
            match = None  # split failed (e.g. the "time" block was actually
            # part of a no-time ticker's team-code blob) — fall through

    if match is None:
        match = _TICKER_NO_TIME_RE.match(ticker)
        if not match:
            return None
        groups = match.groupdict()
        away_code, home_code = _split_matchup(groups["teams"], known_codes)
        if away_code is None:
            return None

    side_code = groups["side"]
    if side_code not in known_codes:
        return None

    try:
        event_date = datetime.strptime(
            f"{groups['yy']}{groups['mon']}{groups['dd']}", "%y%b%d"
        ).date()
    except ValueError:
        return None

    return {
        "ticker": ticker,
        "series": groups["series"],
        "event_date": event_date,
        "embedded_time": embedded_time,  # (hour, minute) in ET, or None
        "away_code": away_code,
        "home_code": home_code,
        "away_team": team_map.get(away_code),
        "home_team": team_map.get(home_code),
        "side_code": side_code,
        "side_team": team_map.get(side_code),
    }


def match_to_schedule_game(
    parsed: dict, schedule_games: list[dict], date_tolerance_days: int = 0
) -> Optional[dict]:
    """Finds the schedule row (from src/fetchers/schedule.py output) for a
    parsed Kalshi ticker, by team pair + date.

    schedule_games rows are expected to have: home_team, away_team,
    start_time_utc (ISO string, authoritative kickoff), game_id,
    home_score, away_score, status.

    date_tolerance_days is a safety margin on top of the ET-date
    comparison below, for any remaining edge cases — leave at 0 unless
    you see unexplained unmatched games clustered near date boundaries.
    """
    if parsed is None:
        return None

    ticker_teams = {parsed["away_team"], parsed["home_team"]}
    candidates = []
    for game in schedule_games:
        if {game["home_team"], game["away_team"]} != ticker_teams:
            continue
        game_start = datetime.fromisoformat(game["start_time_utc"].replace("Z", "+00:00"))
        # Kalshi's ticker date is the game's ET calendar date (confirmed by
        # the original MLB matcher's use of EASTERN for exactly this
        # reason) — a 9:40pm ET start is still "that night's" ticker date
        # even though it's after midnight UTC. Comparing raw UTC dates
        # against the ticker's naive date would misclassify any night
        # game as a day off, so convert to ET first.
        game_date_et = game_start.astimezone(EASTERN).date()
        day_diff = abs((game_date_et - parsed["event_date"]).days)
        if day_diff <= date_tolerance_days:
            candidates.append((day_diff, game))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][1]

    # Multiple same-day games between the same two teams (doubleheader) —
    # only MLB tickers carry an embedded time to disambiguate this. NFL
    # never hits this branch (no embedded time, and no doubleheaders).
    if parsed["embedded_time"] is not None:
        target_hour, target_minute = parsed["embedded_time"]
        target_total_minutes = target_hour * 60 + target_minute

        def _et_minute_diff(candidate):
            _, game = candidate
            game_start = datetime.fromisoformat(
                game["start_time_utc"].replace("Z", "+00:00")
            )
            et_start = game_start.astimezone(EASTERN)
            game_total_minutes = et_start.hour * 60 + et_start.minute
            return abs(game_total_minutes - target_total_minutes)

        candidates.sort(key=_et_minute_diff)
    return candidates[0][1]
