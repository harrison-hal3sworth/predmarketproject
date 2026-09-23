"""
extract_team_codes.py — generalized version of the original MLB-only
script. Pulls every distinct team code Kalshi actually uses for a given
series and prints which ones are missing from (or extra in) your
lookup table, so you can verify a lookup table before trusting it at
scale — this is the check the NFL team table (pre-filled from general
knowledge, not yet confirmed against live data beyond the ~10 codes
seen in chat) specifically needs.

Usage:
    python scripts/extract_team_codes.py --series KXNFLGAME --team-map nfl_teams.json
    python scripts/extract_team_codes.py --series KXMLBGAME --team-map mlb_teams.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TEAM_MAP_DIR = Path(__file__).resolve().parent.parent / "data" / "lookup_tables"


def fetch_codes(series_ticker: str) -> set[str]:
    codes = set()
    cursor = None
    while True:
        params = {"series_ticker": series_ticker, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://api.elections.kalshi.com/trade-api/v2/historical/markets", params=params, timeout=30
        )
        resp.raise_for_status()
        payload = resp.json()
        for m in payload.get("markets", []):
            match = re.search(r"-([A-Z]+)$", m["ticker"])
            if match:
                codes.add(match.group(1))
        cursor = payload.get("cursor")
        if not cursor or not payload.get("markets"):
            break
    return codes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", required=True, help="e.g. KXNFLGAME")
    parser.add_argument("--team-map", required=True, help="e.g. nfl_teams.json")
    args = parser.parse_args()

    live_codes = fetch_codes(args.series)
    print(f"{len(live_codes)} distinct codes seen live for {args.series}:")
    for code in sorted(live_codes):
        print(" ", code)

    map_path = TEAM_MAP_DIR / args.team_map
    if map_path.exists():
        table = json.loads(map_path.read_text())
        table_codes = set(table.keys())

        missing = live_codes - table_codes
        extra = table_codes - live_codes
        if missing:
            print(f"\nIn Kalshi's data but NOT in {args.team_map} (add these):")
            for code in sorted(missing):
                print(" ", code)
        if extra:
            print(f"\nIn {args.team_map} but not seen live yet (fine if it's just early-season "
                  f"undersampling — re-run later in the season before assuming these are wrong):")
            for code in sorted(extra):
                print(" ", code)
        if not missing and not extra:
            print(f"\n{args.team_map} matches every code seen live. ")
    else:
        print(f"\nNo existing table at {map_path} — writing a skeleton for you to fill in.")
        map_path.write_text(json.dumps({code: "" for code in sorted(live_codes)}, indent=2))


if __name__ == "__main__":
    main()
