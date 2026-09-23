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

OPTIMIZED 2026-09-23 (two independent fixes, both real but for different
reasons — see the "suspiciously long" [4/4] investigation in chat):

  1. _group_kalshi_tickers_by_game() used to do an unindexed linear scan
     of the full schedule_games list for every parsed Kalshi ticker
     (~2,460 markets x ~1,230 games for a full NBA season ~= ~3M
     comparisons). That's pure in-memory Python so it wasn't actually
     the bottleneck you were seeing, but it's a real O(n*m) -> ~O(n) fix
     and it's free, so it's included here too: schedule_games is now
     pre-indexed by team name so each market only scans the games that
     team actually played (a handful) before handing that short list to
     the unmodified match_to_schedule_game() to pick the right one by
     date proximity.

  2. build_dataset() used to call kalshi_history.pick_pregame_candle()
     once per matched game-side, sequentially — a real network HTTP GET
     with its own built-in delay, up to ~2,460 of them for a full
     season, no concurrency, no progress output. THIS was the actual
     "suspiciously long" [4/4] you saw in Task Manager: real work, just
     one request at a time. Fixed by collecting every needed fetch
     up front and running them through a small ThreadPoolExecutor
     (_CANDLE_FETCH_WORKERS = 4) instead — enough to meaningfully
     parallelize a network-bound call without pushing the aggregate
     request rate over Kalshi's documented basic-tier ceiling
     (~20 req/sec — see kalshi_history.py's module comments). Progress
     is now printed every 100 completions so a long run isn't silent.
"""

import concurrent.futures
from datetime import datetime

from src.fetchers import kalshi_history
from src.matching.event_matcher import parse_kalshi_ticker, match_to_schedule_game

# Kept deliberately small. kalshi_history.py's own _REQUEST_DELAY_SECONDS
# (0.1s) caps each *worker's* own request rate at ~10/sec, but that delay
# doesn't coordinate across threads — real wall-clock per request (network
# round-trip, not just the sleep) is what actually keeps 4 concurrent
# workers under the ~20 req/sec basic-tier ceiling in practice. Raise this
# only alongside a real shared rate limiter, not just a bigger number.
_CANDLE_FETCH_WORKERS = 4


def _group_kalshi_tickers_by_game(
    kalshi_markets: list[dict], team_map_file: str, schedule_games: list[dict]
) -> dict[str, dict]:
    """Parses every Kalshi market ticker and matches it to a schedule
    game_id. Returns {game_id: {"home": parsed_or_None, "away": parsed_or_None}}.

    Pre-indexes schedule_games by team name (a game is filed under both
    its home and away team) so each parsed ticker only has to scan the
    games *that one team* played — instead of every scheduled game in
    the whole date range — before match_to_schedule_game() (unchanged)
    picks the right one by date proximity. Only relies on parsed["side_team"]
    existing, same as the code below it already did, so it doesn't need
    to know anything else about parse_kalshi_ticker()'s internals.
    """
    games_by_team: dict[str, list[dict]] = {}
    for game in schedule_games:
        games_by_team.setdefault(game["home_team"], []).append(game)
        games_by_team.setdefault(game["away_team"], []).append(game)

    grouped: dict[str, dict] = {}

    for market in kalshi_markets:
        parsed = parse_kalshi_ticker(market["ticker"], team_map_file)
        if parsed is None:
            continue

        candidate_games = games_by_team.get(parsed["side_team"], [])
        matched_game = match_to_schedule_game(parsed, candidate_games)
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


def _fetch_candle_job(job: tuple[str, str, str, datetime], candle_window_minutes: int):
    """Runs inside a worker thread — one real Kalshi HTTP GET via
    kalshi_history.pick_pregame_candle(). Returns (game_id, side, candle)
    so the caller can assemble the {(game_id, side): candle} map without
    the workers needing to share any mutable state."""
    game_id, side, ticker, start_time = job
    candle = kalshi_history.pick_pregame_candle(ticker, start_time, candle_window_minutes)
    return game_id, side, candle


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

    # Collect every candle fetch this run needs before making any of them,
    # so they can go through a thread pool instead of one ticker at a time.
    fetch_jobs: list[tuple[str, str, str, datetime]] = []
    for game_id, group in grouped.items():
        schedule_game = group["schedule"]
        start_time = datetime.fromisoformat(schedule_game["start_time_utc"].replace("Z", "+00:00"))

        if group["home"]:
            fetch_jobs.append((game_id, "home", group["home"]["market"]["ticker"], start_time))
        if group["away"]:
            fetch_jobs.append((game_id, "away", group["away"]["market"]["ticker"], start_time))

    candles: dict[tuple[str, str], dict | None] = {}
    total_jobs = len(fetch_jobs)
    if total_jobs:
        print(f"      Fetching {total_jobs} Kalshi candles "
              f"({_CANDLE_FETCH_WORKERS} concurrent workers)...")

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CANDLE_FETCH_WORKERS) as pool:
        futures = [
            pool.submit(_fetch_candle_job, job, candle_window_minutes)
            for job in fetch_jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            game_id, side, candle = future.result()
            candles[(game_id, side)] = candle
            completed += 1
            if completed % 100 == 0 or completed == total_jobs:
                print(f"      ...{completed}/{total_jobs} candles fetched")

    rows = []
    for game_id, group in grouped.items():
        schedule_game = group["schedule"]

        home_ticker = group["home"]["market"]["ticker"] if group["home"] else None
        away_ticker = group["away"]["market"]["ticker"] if group["away"] else None
        home_candle = candles.get((game_id, "home"))
        away_candle = candles.get((game_id, "away"))

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
