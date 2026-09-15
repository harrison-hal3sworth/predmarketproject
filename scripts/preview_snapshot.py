"""
preview_snapshot.py — manual sanity check of the full non-storage pipeline:
pulls live Kalshi markets + cached TheOddsAPI data, matches them, computes
both probabilities for each side, and prints the gap. No Excel writing, no
scheduling, no bet logic — just "does the math look right on real games."
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.matching.event_matcher import parse_kalshi_ticker, match_to_odds_game
from src.pricing.devig import american_odds_to_prob, devig_two_way
from src.pricing.kalshi_prob import kalshi_price_to_prob

ODDS_CACHE = Path(__file__).resolve().parent / "odds_sample.json"


def get_moneyline_odds(game: dict):
    """Pulls the (home_price, away_price) American odds out of a cached
    TheOddsAPI game dict, from whichever book was queried (draftkings)."""
    for book in game.get("books", []):
        if book["market"] == "h2h":
            prices = {o["name"]: o["price"] for o in book["outcomes"]}
            return prices.get(game["home_team"]), prices.get(game["away_team"])
    return None, None


def main():
    kalshi_resp = requests.get(
        "https://api.elections.kalshi.com/trade-api/v2/markets",
        params={"series_ticker": "KXMLBGAME", "status": "open", "limit": 1000},
    )
    kalshi_resp.raise_for_status()
    kalshi_markets = kalshi_resp.json()["markets"]

    odds_games = json.loads(ODDS_CACHE.read_text())

    for market in kalshi_markets:
        parsed = parse_kalshi_ticker(market["ticker"])
        if parsed is None:
            continue

        game = match_to_odds_game(parsed, odds_games)
        if game is None:
            continue

        home_price, away_price = get_moneyline_odds(game)
        if home_price is None or away_price is None:
            continue

        home_raw = american_odds_to_prob(home_price)
        away_raw = american_odds_to_prob(away_price)
        fair_home, fair_away = devig_two_way(home_raw, away_raw)

        side_team = parsed["side_team"]
        fair_prob = fair_home if side_team == game["home_team"] else fair_away

        kalshi_prob = kalshi_price_to_prob(market["last_price_dollars"])
        gap_pct = (kalshi_prob - fair_prob) * 100

        print(
            f"{side_team:25s} | Kalshi: {kalshi_prob:.3f} | "
            f"Sportsbook fair: {fair_prob:.3f} | gap: {gap_pct:+.1f} pts"
        )


if __name__ == "__main__":
    main()