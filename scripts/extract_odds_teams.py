# CLAUDE GENERATED CODE TO EXTRACT TEAM CODES FROM THE ODDS API

"""
extract_odds_teams.py — lists every distinct MLB team name TheOddsAPI
uses, pulled from the cached odds_sample.json (no live API call).
"""

import json
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "odds_sample.json"

if not CACHE_FILE.exists():
    raise SystemExit(f"No cached data at {CACHE_FILE}. Run scripts/smoke_test.py first.")

games = json.loads(CACHE_FILE.read_text())

teams = set()
for g in games:
    teams.add(g["home_team"])
    teams.add(g["away_team"])

for team in sorted(teams):
    print(team)