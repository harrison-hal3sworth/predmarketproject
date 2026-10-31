"""
Build the modeling table: one row per game, home-team perspective.

Run from the project root:
    python analysis/build_model_table.py

Reads   data/{sport}_2025_historical.csv  (raw pipeline output)
Writes  data/model_table.csv

Applies the same filters as cleaning/cleaning.ipynb (so the row count should
match data/clean_data.csv), but keeps game_id and the raw sportsbook odds, and
adds what the models need: every de-vig method, logit-scale probabilities and
gap, season phase, platform age, and same-day competition.

Column glossary
---------------
Identity / outcome
  game_id, sport, home_team, away_team, start_time_utc
  start_time_et, game_date_et   kickoff in US Eastern; game_date_et is the
                                clustering unit for standard errors
  home_win                      1 if the home team won

Kalshi (prices at the close of the last pre-kickoff candle)
  kalshi_{home,away}_{bid,ask,mid}
  kalshi_overround              home_mid + away_mid
  p_kalshi                      home_mid / overround   (normalized, sums to 1)
  kalshi_ba_spread              average bid-ask spread of the two contracts

Sportsbook (bet365)
  book_{home,away}_odds         American odds
  book_overround                sum of raw implied probabilities
  p_book_<method>               de-vigged home probability for each method in
                                DEVIG_METHODS
  p_book                        = p_book_<PRIMARY_DEVIG> (the pre-registered one)

Gap (sign convention kept from EDA: sportsbook minus Kalshi)
  gap_pp                        100 * (p_book - p_kalshi), percentage points
  logit_kalshi, logit_book      log(p / (1 - p))
  logit_gap                     logit_book - logit_kalshi   (a log odds ratio)
  abs_logit_gap                 |logit_gap|
  lopsided                      |2 * p_avg - 1|, p_avg = mean of the two sources

Liquidity (log1p so zero-volume games survive)
  total_volume, total_open_interest, log_volume, log_open_interest
  log_turnover                  log_volume - log_open_interest
  low_volume                    total_volume < MIN_VOLUME (flag, not a filter)

Time / season controls
  month                         YYYY-MM
  phase                         regular | play_in | playoff
  postseason                    phase != regular
  season_progress               0 -> 1 across the sport's regular season
                                (postseason rows = 1)
  months_since_start            months since the first game in the dataset
                                (proxy for Kalshi platform maturity)
  n_sports_today                number of leagues with a game that ET date
  n_games_today                 total games (all leagues) that ET date
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from src.pricing.devig import american_odds_to_prob, devig_two_way  # noqa: E402

# ---------------------------------------------------------------- settings
SPORTS = ["nfl", "mlb", "nba", "nhl"]
SEASON = 2025
MIN_VOLUME = 100
EPS = 1e-4  # clip before logit so p = 0 or 1 never blows up

# Fixed up front (no optimizing over this). The rest feed the sensitivity table.
# Note: for a two-outcome market Shin's method gives exactly the additive answer,
# so "shin" and "additive" columns will be identical.
PRIMARY_DEVIG = "cheung"
DEVIG_METHODS = ["proportional", "additive", "shin", "power", "cheung"]

# First postseason date (US Eastern), read off the daily game counts in the raw
# data and consistent with the 2025 / 2025-26 league calendars.
POSTSEASON_START = {
    "mlb": "2025-09-30",   # Wild Card Series
    "nfl": "2026-01-10",   # Wild Card weekend
    "nba": "2026-04-14",   # play-in tournament (playoffs proper from 04-18)
    "nhl": "2026-04-18",
}
NBA_PLAYOFFS_START = "2026-04-18"


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def load_raw() -> pd.DataFrame:
    frames = []
    for sport in SPORTS:
        df = pd.read_csv(ROOT / "data" / f"{sport}_{SEASON}_historical.csv")
        df["sport"] = sport
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw["start_time_utc"] = pd.to_datetime(raw["start_time_utc"], utc=True)
    raw["start_time_et"] = raw["start_time_utc"].dt.tz_convert("America/New_York")
    raw["game_date_et"] = raw["start_time_et"].dt.strftime("%Y-%m-%d")
    return raw


def add_competition(raw: pd.DataFrame) -> pd.DataFrame:
    """Same-day competition, counted on the full raw schedule (before filtering),
    since a game competes for attention whether or not we have its prices."""
    per_day = raw.groupby("game_date_et").agg(
        n_sports_today=("sport", "nunique"),
        n_games_today=("sport", "size"),
    )
    return raw.join(per_day, on="game_date_et")


def add_season_controls(df: pd.DataFrame) -> pd.DataFrame:
    date = pd.to_datetime(df["game_date_et"])
    post_start = pd.to_datetime(df["sport"].map(POSTSEASON_START))

    df["phase"] = np.where(date >= post_start, "playoff", "regular")
    is_play_in = (df["sport"] == "nba") & (date >= post_start) & (date < pd.Timestamp(NBA_PLAYOFFS_START))
    df.loc[is_play_in, "phase"] = "play_in"
    df["postseason"] = df["phase"] != "regular"

    # Regular-season progress, 0 at the sport's first game, 1 at its last
    # regular-season game. Computed on the full schedule so it reflects the
    # calendar, not which games survived filtering.
    reg = df[~df["postseason"]]
    first = reg.groupby("sport")["start_time_utc"].min()
    last = reg.groupby("sport")["start_time_utc"].max()
    span = (df["sport"].map(last) - df["sport"].map(first)).dt.total_seconds()
    since = (df["start_time_utc"] - df["sport"].map(first)).dt.total_seconds()
    df["season_progress"] = np.where(df["postseason"], 1.0, since / span).clip(0, 1)

    t0 = df["start_time_utc"].min()
    df["months_since_start"] = (df["start_time_utc"] - t0).dt.total_seconds() / (30.44 * 86400)
    df["month"] = df["start_time_et"].dt.strftime("%Y-%m")
    return df


def apply_filters(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same filters as cleaning.ipynb. Returns (kept rows, retention log)."""
    log = []
    required = []
    for side in ["home", "away"]:
        required += [f"kalshi_{side}_yes_bid_close", f"kalshi_{side}_yes_ask_close",
                     f"kalshi_{side}_volume", f"kalshi_{side}_open_interest",
                     f"sportsbook_{side}_odds_american"]

    def step(name, mask):
        nonlocal df
        log.append({"step": name, "dropped": int((~mask).sum())})
        df = df[mask]

    step("missing prices / odds", df[required].notna().all(axis=1))
    step("no outcome or tie", df["home_score"].notna() & df["away_score"].notna()
         & (df["home_score"] != df["away_score"]))
    mids = (df["kalshi_home_yes_bid_close"] + df["kalshi_home_yes_ask_close"]
            + df["kalshi_away_yes_bid_close"] + df["kalshi_away_yes_ask_close"]) / 2
    step("empty Kalshi book", mids > 0)
    return df.copy(), pd.DataFrame(log)


def build() -> pd.DataFrame:
    raw = load_raw()
    raw = add_competition(raw)
    raw = add_season_controls(raw)
    df, retention = apply_filters(raw)

    out = df[["game_id", "sport", "home_team", "away_team", "start_time_utc",
              "start_time_et", "game_date_et", "home_score", "away_score",
              "phase", "postseason", "season_progress", "months_since_start", "month",
              "n_sports_today", "n_games_today"]].copy()
    out["home_win"] = (df["home_score"] > df["away_score"]).astype(int)

    # Kalshi
    for side in ["home", "away"]:
        out[f"kalshi_{side}_bid"] = df[f"kalshi_{side}_yes_bid_close"]
        out[f"kalshi_{side}_ask"] = df[f"kalshi_{side}_yes_ask_close"]
        out[f"kalshi_{side}_mid"] = (out[f"kalshi_{side}_bid"] + out[f"kalshi_{side}_ask"]) / 2
    out["kalshi_overround"] = out["kalshi_home_mid"] + out["kalshi_away_mid"]
    out["p_kalshi"] = out["kalshi_home_mid"] / out["kalshi_overround"]
    out["kalshi_ba_spread"] = ((out["kalshi_home_ask"] - out["kalshi_home_bid"])
                               + (out["kalshi_away_ask"] - out["kalshi_away_bid"])) / 2

    # Sportsbook
    out["book_home_odds"] = df["sportsbook_home_odds_american"].astype(int)
    out["book_away_odds"] = df["sportsbook_away_odds_american"].astype(int)
    raw_home = out["book_home_odds"].map(american_odds_to_prob)
    raw_away = out["book_away_odds"].map(american_odds_to_prob)
    out["book_overround"] = raw_home + raw_away
    for method in DEVIG_METHODS:
        out[f"p_book_{method}"] = [
            float(devig_two_way(h, a, de_vig_strat=method)[0])
            for h, a in zip(raw_home, raw_away)
        ]
    out["p_book"] = out[f"p_book_{PRIMARY_DEVIG}"]

    # Gap
    out["gap_pp"] = 100 * (out["p_book"] - out["p_kalshi"])
    out["logit_kalshi"] = logit(out["p_kalshi"])
    out["logit_book"] = logit(out["p_book"])
    out["logit_gap"] = out["logit_book"] - out["logit_kalshi"]
    out["abs_logit_gap"] = out["logit_gap"].abs()
    out["lopsided"] = (out["p_kalshi"] + out["p_book"] - 1).abs()  # = |2*p_avg - 1|

    # Liquidity
    out["total_volume"] = df["kalshi_home_volume"] + df["kalshi_away_volume"]
    out["total_open_interest"] = df["kalshi_home_open_interest"] + df["kalshi_away_open_interest"]
    out["log_volume"] = np.log1p(out["total_volume"])
    out["log_open_interest"] = np.log1p(out["total_open_interest"])
    out["log_turnover"] = out["log_volume"] - out["log_open_interest"]
    out["low_volume"] = out["total_volume"] < MIN_VOLUME

    out = out.sort_values("start_time_utc").reset_index(drop=True)

    # Sanity checks
    assert out["game_id"].is_unique, "duplicate games"
    for method in DEVIG_METHODS:
        p = out[f"p_book_{method}"]
        assert p.between(0, 1).all(), f"{method} produced probabilities outside [0, 1]"
    assert out["p_kalshi"].between(0, 1).all()

    print("Rows dropped by filter:")
    print(retention.to_string(index=False))
    print(f"\nFinal rows: {len(out)}")
    print(pd.crosstab(out["sport"], out["phase"], margins=True).to_string())
    return out


if __name__ == "__main__":
    table = build()
    path = ROOT / "data" / "model_table.csv"
    table.to_csv(path, index=False)
    print(f"\nWrote {path.relative_to(ROOT)}  ({table.shape[0]} rows x {table.shape[1]} cols)")
