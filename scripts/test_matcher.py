# CLAUDE GENERATED CODE TO TEST EVENT MATCHER
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.matching.event_matcher import parse_kalshi_ticker, match_to_odds_game

import requests

resp = requests.get(
    "https://api.elections.kalshi.com/trade-api/v2/markets",
    params={"series_ticker": "KXMLBGAME", "status": "open", "limit": 1000},
)
resp.raise_for_status()
kalshi_markets = resp.json()["markets"]

odds_games = json.loads((Path(__file__).resolve().parent / "odds_sample.json").read_text())


# parse_failures = 0
# parsed_dates = set()
# for m in kalshi_markets:
#     parsed = parse_kalshi_ticker(m["ticker"])
#     if parsed is None:
#         parse_failures += 1
#     else:
#         parsed_dates.add(parsed["start_time"].date())

# print(f"Parse failures: {parse_failures} / {len(kalshi_markets)}")
# print("Dates in Kalshi data:", sorted(parsed_dates))

# odds_dates = {datetime.fromisoformat(g["start_time"].replace("Z", "+00:00")).date() for g in odds_games}
# print("Dates in cached odds data:", sorted(odds_dates))

# team_only_matches = 0
# for m in kalshi_markets:
#     parsed = parse_kalshi_ticker(m["ticker"])
#     if not parsed:
#         continue
#     kalshi_teams = {parsed["away_team"], parsed["home_team"]}
#     for game in odds_games:
#         if {game["home_team"], game["away_team"]} == kalshi_teams:
#             odds_start = datetime.fromisoformat(game["start_time"].replace("Z", "+00:00"))
#             diff_minutes = (odds_start - parsed["start_time"]).total_seconds() / 60
#             print(f"{parsed['away_team']} @ {parsed['home_team']} | Kalshi: {parsed['start_time']} | Odds: {odds_start} | diff: {diff_minutes:.0f} min")
#             team_only_matches += 1

# print(f"\n{team_only_matches} team-name-only matches found (time tolerance ignored)")

matched, unmatched = 0, 0
for m in kalshi_markets:
    parsed = parse_kalshi_ticker(m["ticker"])
    game = match_to_odds_game(parsed, odds_games) if parsed else None
    if game:
        matched += 1
        print(parsed["away_team"], "@", parsed["home_team"], "->", "matched, starts", game["start_time"])
    else:
        unmatched += 1

print(f"\n{matched} matched, {unmatched} unmatched (expected — cache only covers one day's games)")