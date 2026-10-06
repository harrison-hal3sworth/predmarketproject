# CLAUDE GENERATED CODE TO EXTRACT SPORT SCHEDULES FOR GIVEN TIME INTERVAL

"""
schedule.py

Ground-truth schedule + final-score puller, using ESPN's public (but
unofficial/undocumented) scoreboard API. This is the authoritative
source for two things nothing else in the pipeline should guess at:
  1. The actual scheduled kickoff time (what "pre-game" means for
     picking a Kalshi candlestick)
  2. The final result (what "settlement" means for a historical row —
     no need for a separate polling job, the result is already known)

ESPN's scoreboard endpoint takes one date per request:
  https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD

This module iterates day-by-day over a range, which is simple and
robust but means a full NFL season (~19 weeks) is ~130+ requests rather
than a handful of week-based ones. That's a fine tradeoff for a
one-sport pilot; if it's too slow once you're scaling to other sports,
switch to the week-based form (?week=N&seasontype=2&year=YYYY) — that's
an NFL-specific parameter shape (other sports use plain date ranges),
which is exactly the kind of sport-specific wrinkle to expect from a
third-party scoreboard API and confirm empirically rather than assume.

No auth needed. Will NOT work from inside the Claude cloud session
(same network restriction as kalshi_history.py) — run locally.
"""

import time
from datetime import date, datetime, timedelta, timezone

import requests

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"

# sport slug -> (espn_sport, espn_league)
SPORT_ENDPOINTS = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
}


def _fetch_day(espn_sport: str, espn_league: str, day: date) -> list[dict]:
    url = f"{BASE_URL}/{espn_sport}/{espn_league}/scoreboard"
    resp = requests.get(url, params={"dates": day.strftime("%Y%m%d")}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("events", [])


def _parse_event(event: dict) -> dict | None:
    try:
        competition = event["competitions"][0]
        competitors = {c["homeAway"]: c for c in competition["competitors"]}
        home, away = competitors["home"], competitors["away"]
        status = competition["status"]["type"]

        return {
            "game_id": event["id"],
            "start_time_utc": event["date"],  # already ISO 8601 UTC, e.g. "2026-09-21T17:00Z"
            "home_team": home["team"]["displayName"],
            "away_team": away["team"]["displayName"],
            "home_score": int(home["score"]) if status.get("completed") else None,
            "away_score": int(away["score"]) if status.get("completed") else None,
            "status": status.get("name"),  # e.g. "STATUS_FINAL"
            "completed": status.get("completed", False),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        # Malformed/unexpected event shape — surface it rather than
        # silently skipping, since a schema change here would otherwise
        # quietly shrink your dataset.
        print(f"  WARNING: couldn't parse event {event.get('id')}, skipping")
        return None


def get_schedule(sport: str, start_date: date, end_date: date, request_delay: float = 0.2) -> list[dict]:
    """Pulls every game (with final score, if completed) for `sport`
    between start_date and end_date inclusive. `sport` must be a key in
    SPORT_ENDPOINTS.

    Duplicate games (ESPN sometimes lists the same event on adjacent
    days near midnight UTC boundaries) are de-duplicated by game_id.
    """
    if sport not in SPORT_ENDPOINTS:
        raise ValueError(f"Unknown sport '{sport}' — add it to SPORT_ENDPOINTS first")
    espn_sport, espn_league = SPORT_ENDPOINTS[sport]

    games_by_id: dict[str, dict] = {}
    day = start_date
    while day <= end_date:
        events = _fetch_day(espn_sport, espn_league, day)
        for event in events:
            parsed = _parse_event(event)
            if parsed:
                games_by_id[parsed["game_id"]] = parsed
        time.sleep(request_delay)
        day += timedelta(days=1)

    return sorted(games_by_id.values(), key=lambda g: g["start_time_utc"])
