# Contains Claude generated code

"""
verify_oddsharvester_sport.py

Checks a --sport / --league / --market combination against the installed
OddsHarvester package's own registries, so a typo in config.yaml (e.g.
"hockey" instead of "ice-hockey") fails here in a second instead of
partway into a long scrape. Checks, in order:

  1. sport   -> oddsharvester.utils.sport_market_constants.Sport enum
  2. league  -> oddsharvester.utils.sport_league_constants.SPORTS_LEAGUES_URLS_MAPPING
  3. market  -> oddsharvester.core.sport_market_registry.SportMarketRegistry
                (populated via SportMarketRegistrar.register_all_markets())

Run before adding a new sport or market (or before a re-scrape with new
flags such as --odds-history):

    python scripts/verify_oddsharvester_sport.py --sport ice-hockey --league nhl --market home_away
    python scripts/verify_oddsharvester_sport.py --from-config      # every sports.* block in config.yaml

Exits 0 if every combination checked is valid, 1 otherwise.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_registries():
    try:
        from oddsharvester.utils.sport_market_constants import Sport
        from oddsharvester.utils.sport_league_constants import SPORTS_LEAGUES_URLS_MAPPING
        from oddsharvester.core.sport_market_registry import (
            SportMarketRegistrar,
            SportMarketRegistry,
        )
    except ImportError as e:
        sys.exit(f"Couldn't import oddsharvester ({e}). Install it with: pip install oddsharvester")

    SportMarketRegistrar.register_all_markets()
    return Sport, SPORTS_LEAGUES_URLS_MAPPING, SportMarketRegistry


def verify(sport: str, league: str, market: str, label: str = "") -> bool:
    Sport, league_map, market_registry = _load_registries()
    prefix = f"[{label}] " if label else ""
    ok = True

    valid_sports = [s.value for s in Sport]
    if sport not in valid_sports:
        print(f"{prefix}FAIL sport '{sport}' — valid sports: {', '.join(valid_sports)}")
        return False
    sport_enum = Sport(sport)
    print(f"{prefix}ok   sport '{sport}'")

    leagues = league_map.get(sport_enum, {})
    if league in leagues:
        print(f"{prefix}ok   league '{league}' -> {leagues[league]}")
    else:
        ok = False
        print(f"{prefix}FAIL league '{league}' not registered for '{sport}' — "
              f"valid leagues: {', '.join(sorted(leagues)) or '(none)'}")

    markets = market_registry.get_market_mapping(sport)
    if market in markets:
        print(f"{prefix}ok   market '{market}'")
    else:
        ok = False
        print(f"{prefix}FAIL market '{market}' not registered for '{sport}' — "
              f"valid markets: {', '.join(sorted(markets)) or '(none)'}")
    return ok


def verify_config(config_path: Path) -> bool:
    import yaml

    cfg = yaml.safe_load(config_path.read_text())
    all_ok = True
    for name, sport_cfg in cfg.get("sports", {}).items():
        missing = [k for k in ("oddsharvester_sport", "oddsharvester_league", "oddsharvester_market")
                   if not sport_cfg.get(k)]
        if missing:
            print(f"[{name}] FAIL config block is missing: {', '.join(missing)}")
            all_ok = False
            continue
        all_ok &= verify(sport_cfg["oddsharvester_sport"], sport_cfg["oddsharvester_league"],
                         sport_cfg["oddsharvester_market"], label=name)
    return all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--sport", help='OddsHarvester sport, e.g. "ice-hockey"')
    parser.add_argument("--league", help='OddsHarvester league, e.g. "nhl"')
    parser.add_argument("--market", default="home_away", help='market, default "home_away"')
    parser.add_argument("--from-config", action="store_true",
                        help="check every sports.* block in config/config.yaml instead")
    parser.add_argument("--config", default=str(ROOT / "config" / "config.yaml"))
    args = parser.parse_args()

    if args.from_config:
        ok = verify_config(Path(args.config))
    elif args.sport and args.league:
        ok = verify(args.sport, args.league, args.market)
    else:
        parser.error("give --sport and --league, or --from-config")

    print("\nAll checks passed." if ok else "\nSome checks failed — see FAIL lines above.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
