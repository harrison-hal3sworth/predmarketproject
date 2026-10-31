# Contains Claude generated code

"""
verify_team_names.py — generalized, and repointed at ESPN's schedule
data instead of TheOddsAPI (the join now happens against schedule.py's
output, not a live odds feed — see event_matcher.match_to_schedule_game).

Checks that every full team name in a lookup table exactly matches a
team name ESPN actually uses, so event_matcher.py's team-set comparison
doesn't silently fail on something like "LA Rams" vs "Los Angeles Rams".

Example Usage:
    python scripts/verify_team_names.py --sport nfl --team-map nfl_teams.json \
        --start 2025-09-01 --end 2025-09-14
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.fetchers.schedule import get_schedule

TEAM_MAP_DIR = Path(__file__).resolve().parent.parent / "data" / "lookup_tables"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, help="e.g. nfl")
    parser.add_argument("--team-map", required=True, help="e.g. nfl_teams.json")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    mapping = json.loads((TEAM_MAP_DIR / args.team_map).read_text())
    games = get_schedule(args.sport, start_date, end_date)

    espn_teams = set()
    for g in games:
        espn_teams.add(g["home_team"])
        espn_teams.add(g["away_team"])

    mismatches = [name for name in mapping.values() if name not in espn_teams]
    if mismatches:
        print("These lookup-table names don't exactly match any ESPN team name seen "
              "in this date range:")
        for name in mismatches:
            print(" -", repr(name))
    else:
        print("All lookup-table names present in this sample matched ESPN team names exactly.")

    missing_from_table = espn_teams - set(mapping.values())
    if missing_from_table:
        print("\n(ESPN team names seen but not checked against — not every team plays "
              "in a short date range; widen --start/--end for full coverage)")
        print(sorted(missing_from_table))


if __name__ == "__main__":
    main()
