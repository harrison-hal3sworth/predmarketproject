# # CLAUDE GENERATED CODE TO VERIFY TEAM NAMES

"""
verify_team_names.py — checks that every full name in mlb_teams.json
exactly matches a team name that actually appears in the cached
TheOddsAPI data, so event_matcher.py doesn't silently fail on a typo.
"""

import json
from pathlib import Path

MAPPING_FILE = Path(__file__).resolve().parent.parent / "data" / "lookup_tables" / "mlb_teams.json"
ODDS_CACHE = Path(__file__).resolve().parent / "odds_sample.json"

mapping = json.loads(MAPPING_FILE.read_text())
games = json.loads(ODDS_CACHE.read_text())

odds_teams = set()
for g in games:
    odds_teams.add(g["home_team"])
    odds_teams.add(g["away_team"])

mismatches = [name for name in mapping.values() if name not in odds_teams]

if mismatches:
    print("These mapped names don't exactly match any team in the cached odds data:")
    for name in mismatches:
        print(" -", repr(name))
else:
    print("All mapped names match teams seen in the cached odds data (for teams present in this sample).")

missing_from_cache = odds_teams - set(mapping.values())
if missing_from_cache:
    print("\n(Note: these odds teams weren't checked against — not every team appears in a single day's cache)")
    print(missing_from_cache)