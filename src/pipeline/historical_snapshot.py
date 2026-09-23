"""
historical_snapshot.py

Joins schedule + Kalshi + sportsbook data into one row per game. This
is the boundary of the "data curation" work — it outputs raw facts
(prices, odds, volumes, final scores) and does NOT compute implied
probability, de-vig, edge, or any bet decision. That's deliberately
left to your own analysis code, reading the CSV this produces plus
src/pricing/devig.py (already yours) and whatever you build in
src/pipeline/bet_logic.py.

Pipeline, per game:
  1. Take the schedule row as ground truth (team names, kickoff time,
     final score) — see schedule.py.
  2. Find that game's two Kalshi tickers (one per side) — see
     event_matcher.py. Pull the candle nearest kickoff for each, via
     kalshi_history.pick_pregame_candle().
  3. Find that game's sportsbook closing line — see
     sportsbook_history.py. Matched by team pair + date proximity, same
     pattern as the Kalshi match.
  4. Emit one row with everything found. A game missing either side is
     still emitted (with those fields null) rather than dropped — better
     to see gaps in the CSV than silently lose games; you decide during
     analysis whether to filter incomplete rows.
"""

from datetime import datetime

from src.fetchers import kalshi_history
from src.matching.event_matcher import parse_kalshi_ticker, match_to_schedule_game


def _group_kalshi_tickers_by_game(
    kalshi_markets: list[dict], team_map_file: str, schedule_games: list[dict]
) -> dict[str, dict]:
    """Parses every Kalshi market ticker and matches it to a schedule
    game_id. Returns {game_id: {"home": parsed_or_None, "away": parsed_or_None}}."""
    grouped: dict[str, dict] = {}

    for market in kalshi_markets:
        parsed = parse_kalshi_ticker(market["ticker"], team_map_file)
        if parsed is None:
            continue
        matched_game = match_to_schedule_game(parsed, schedule_games)
        if matched_game is None:
            continue

        game_id = matched_game["game_id"]
        grouped.setdefault(game_id, {"home": None, "away": None, "schedule": matched_game})

        if parsed["side_team"] == matched_game["home_team"]:
            grouped[game_id]["home"] = {"market": market, "parsed": parsed}
        elif parsed["side_team"] == matched_game["away_team"]:
            grouped[game_id]["away"] = {"market": market, "parsed": parsed}

    return grouped


def _match_sportsbook_row(
    schedule_game: dict, sportsbook_rows: list[dict], date_tolerance_hours: float = 36
) -> dict | None:
    """Matches a schedule game to a normalized sportsbook_history row by
    team pair + start-time proximity. Wider tolerance than the Kalshi
    match (36h vs same-day) because oddsportal's listed kickoff time may
    be in a different timezone convention than ESPN's — verify this
    against a handful of known games rather than trusting it blind."""
    game_teams = {schedule_game["home_team"], schedule_game["away_team"]}
    game_start = datetime.fromisoformat(schedule_game["start_time_utc"].replace("Z", "+00:00"))

    best, best_diff = None, None
    for row in sportsbook_rows:
        if {row["home_team"], row["away_team"]} != game_teams:
            continue
        row_start = datetime.fromisoformat(row["start_time_utc"].replace("Z", "+00:00"))
        diff_hours = abs((row_start - game_start).total_seconds()) / 3600
        if diff_hours <= date_tolerance_hours and (best_diff is None or diff_hours < best_diff):
            best, best_diff = row, diff_hours
    return best


def _f(x):
    """Candle numeric fields come back as strings (e.g. "0.7800"), or
    null when a period had no trades — see debug findings 2026-09-22."""
    return float(x) if x is not None else None


def _candle_fields(prefix: str, candle: dict | None) -> dict:
    if candle is None:
        return {
            f"{prefix}_candle_end_ts": None,
            f"{prefix}_close_price": None,
            f"{prefix}_yes_bid_close": None,
            f"{prefix}_yes_ask_close": None,
            f"{prefix}_volume": None,
        }
    return {
        f"{prefix}_candle_end_ts": candle["end_period_ts"],
        f"{prefix}_close_price": _f(candle["price"]["close"]),
        f"{prefix}_yes_bid_close": _f(candle["yes_bid"]["close"]),
        f"{prefix}_yes_ask_close": _f(candle["yes_ask"]["close"]),
        f"{prefix}_volume": _f(candle["volume"]),
    }


def build_dataset(
    team_map_file: str,
    schedule_games: list[dict],
    kalshi_markets: list[dict],
    sportsbook_rows: list[dict],
    candle_window_minutes: int = 30,
) -> list[dict]:
    """Main entry point. Returns a list of flat dicts, one per scheduled
    game, ready for storage.csv_writer.write_rows().

    (series_ticker was dropped from this signature on 2026-09-22 — the
    historical candlesticks endpoint doesn't take it in the path, unlike
    the live one this was first built against. See kalshi_history.py's
    module docstring for the full story.)"""
    grouped = _group_kalshi_tickers_by_game(kalshi_markets, team_map_file, schedule_games)

    rows = []
    for game_id, group in grouped.items():
        schedule_game = group["schedule"]

        home_candle = away_candle = None
        home_ticker = away_ticker = None
        start_time = datetime.fromisoformat(schedule_game["start_time_utc"].replace("Z", "+00:00"))

        if group["home"]:
            home_ticker = group["home"]["market"]["ticker"]
            home_candle = kalshi_history.pick_pregame_candle(
                home_ticker, start_time, candle_window_minutes
            )
        if group["away"]:
            away_ticker = group["away"]["market"]["ticker"]
            away_candle = kalshi_history.pick_pregame_candle(
                away_ticker, start_time, candle_window_minutes
            )

        sportsbook_row = _match_sportsbook_row(schedule_game, sportsbook_rows)

        row = {
            "game_id": game_id,
            "home_team": schedule_game["home_team"],
            "away_team": schedule_game["away_team"],
            "start_time_utc": schedule_game["start_time_utc"],
            "home_score": schedule_game["home_score"],
            "away_score": schedule_game["away_score"],
            "kalshi_home_ticker": home_ticker,
            "kalshi_away_ticker": away_ticker,
            **_candle_fields("kalshi_home", home_candle),
            **_candle_fields("kalshi_away", away_candle),
            "sportsbook_bookmaker": sportsbook_row["bookmaker"] if sportsbook_row else None,
            "sportsbook_home_odds_american": sportsbook_row["home_odds_american"] if sportsbook_row else None,
            "sportsbook_away_odds_american": sportsbook_row["away_odds_american"] if sportsbook_row else None,
        }
        rows.append(row)

    # Games with no matched Kalshi ticker at all never entered `grouped` —
    # surface how many were dropped so gaps are visible instead of silent.
    unmatched_count = len(schedule_games) - len(grouped)
    if unmatched_count:
        print(f"  NOTE: {unmatched_count} scheduled games had no matching Kalshi market "
              f"(expected for games before Kalshi's series launch, or byes)")

    return rows
