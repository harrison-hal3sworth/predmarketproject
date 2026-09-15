# CLAUDE GENERATED CODE TO EXTRACT TEAM CODES FROM KALSHI API

"""
extract_team_codes.py — pulling team codes from Kalshi API
and using regexp to find and separate each. Necessary step before matching
team codes from each API
"""
import re
import requests
import json
from pathlib import Path

resp = requests.get(
    "https://api.elections.kalshi.com/trade-api/v2/markets",
    params={"series_ticker": "KXMLBGAME", "status": "open", "limit": 1000},
)
resp.raise_for_status()
markets = resp.json()["markets"]

codes = set()
for m in markets:
    # ticker shape: KXMLBGAME-26SEP152140MIAAZ-MIA
    match = re.search(r"-([A-Z]+)$", m["ticker"])
    if match:
        codes.add(match.group(1))

for code in sorted(codes):
    print(code)

output_path = Path(__file__).resolve().parent.parent / "data" / "lookup_tables" / "mlb_teams.json"
output_path.parent.mkdir(parents=True, exist_ok=True)  # safety net in case the folder isn't there
output_path.write_text(json.dumps({code: "" for code in sorted(codes)}, indent=2))