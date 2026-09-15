# CLAUDE GENERATED CODE TO TEST APIs

"""
smoke_test.py — manual check that both APIs return usable data for the
same real MLB games. No scheduling, no Kalshi auth needed yet (market
data endpoints are public).
"""

import os
from pathlib import Path
import requests
from dotenv import load_dotenv
import datetime
import json


load_dotenv(Path(__file__).resolve().parent.parent / "secrets" / ".env")
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

# 1. Pull today's MLB moneylines from TheOddsAPI (DraftKings only)
CACHE_FILE = Path(__file__).resolve().parent / "odds_sample.json"

if CACHE_FILE.exists():
    odds_games = json.loads(CACHE_FILE.read_text())
else:
    odds_resp = requests.get(
        "https://api.theoddsapi.com/odds/",
        headers={"x-api-key": ODDS_API_KEY},
        params={
            "sport_key": "baseball_mlb",
            "markets": "h2h",
            "regions": "us",
            "bookmakers": "draftkings",
            "oddsFormat": "american",
        },
    )
    odds_resp.raise_for_status()
    odds_games = odds_resp.json()["data"]
    CACHE_FILE.write_text(json.dumps(odds_games, indent=2))

print(f"TheOddsAPI returned {len(odds_games)} MLB games\n")
for g in odds_games[:5]:
    print(g["home_team"], "vs", g["away_team"], "-", g["start_time"])
    for book in g.get("books", []):
        print(" ", book["book"], book["outcomes"])
print()

# 2. Pull open Kalshi MLB game-winner markets (public endpoint, no auth needed)
kalshi_resp = requests.get(
    "https://api.elections.kalshi.com/trade-api/v2/markets",
    params={"series_ticker": "KXMLBGAME", "status": "open"},
)
kalshi_resp.raise_for_status()
kalshi_markets = kalshi_resp.json().get("markets", [])

# Filter to today's games so we're comparing the same slate as TheOddsAPI
today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%y%b%d").upper()  # e.g. "26SEP15"
todays_kalshi = [m for m in kalshi_markets if today_str in m["ticker"]]

print(f"Kalshi returned {len(kalshi_markets)} open MLB markets total, {len(todays_kalshi)} for today\n")
for m in todays_kalshi[:10]:
    print(
        m["ticker"], "-", m.get("yes_sub_title"),
        "- bid:", m.get("yes_bid_dollars"),
        "ask:", m.get("yes_ask_dollars"),
        "last:", m.get("last_price_dollars"),
    )