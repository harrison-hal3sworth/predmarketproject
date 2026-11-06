# Contains Claude generated code

"""
fetch_espn_broadcasts.py

Adds a national-TV indicator for every game in the dataset (the "televised
game" regressor proposed for the slide 8 gap regression). Reads the game
IDs already in data/{sport}_2025_historical.csv (they are ESPN event IDs),
re-requests ESPN's scoreboard for just those game dates, and records each
game's broadcast listing. No scraping and no Kalshi/sportsbook calls: about
one ESPN request per game date (~700 total, a few minutes).

Run locally from the project root (ESPN is blocked from the Claude sandbox):
    python scripts/fetch_espn_broadcasts.py

Writes data/espn_broadcasts.csv, one row per game:
    game_id, sport, national_tv, national_networks, all_networks
then `python analysis/build_model_table.py` merges `national_tv` into
data/model_table.csv.

Definition. national_tv = 1 for a *national exclusive*: ESPN lists at least
one US national broadcast (TV or streaming, e.g. TNT, ESPN, Prime Video) and
NO local or regional broadcast. Out-of-market subscription packages (League
Pass, MLB.TV, Sunday Ticket, ...) and radio (ESPN Radio, listed as "ERADM")
are ignored on both sides. Requiring exclusivity matters: ESPN lists ESPN+
(which carries nearly every out-of-market NHL game), NBA TV and MLB Network
as "national" even when they simulcast the local feed, and counting those
flagged 100% of NHL games. The raw network names are kept in
`national_networks` / `all_networks` so the definition can be audited or
changed later; rerunning the script after a change rebuilds the CSV from the
checkpoint without any requests.

Caveat: ESPN lists Sunday-afternoon NFL games on CBS/FOX as national even
though they are regionally distributed, so national_tv has little variation
within the NFL. Sport fixed effects absorb that; read the within-NFL result
with this in mind.

Resulting shares (2026-11-06 run): MLB 7.6%, NBA 12.3%, NHL 18.5%, NFL 100%.

Resumable: every finished date is appended to
data/espn_broadcasts.checkpoint.ndjson, and a rerun skips those dates.
"""

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.fetchers.schedule import BASE_URL, SPORT_ENDPOINTS  # noqa: E402

SPORTS = ["nfl", "mlb", "nba", "nhl"]
SEASON = 2025
REQUEST_DELAY = 0.2
OUT_PATH = ROOT / "data" / "espn_broadcasts.csv"
CHECKPOINT = ROOT / "data" / "espn_broadcasts.checkpoint.ndjson"

# Out-of-market subscription packages: available everywhere, so not a sign of a
# nationally featured game. Matched case-insensitively as substrings.
OUT_OF_MARKET = ["league pass", "mlb.tv", "power play", "nhl.tv", "sunday ticket", "nfl+", "nba tv app"]
# Radio listings, ignored entirely (ERADM = ESPN Radio).
RADIO = ["eradm", "espn radio", "westwood one", "siriusxm"]


def _ignored(name: str) -> bool:
    n = name.lower()
    return any(x in n for x in OUT_OF_MARKET + RADIO)


def is_national_exclusive(national: list[str], every: list[str]) -> int:
    """1 if there is a national broadcast and every other listing is also national."""
    nat = {n for n in national if not _ignored(n)}
    rest = {n for n in every if not _ignored(n)}
    return int(bool(nat) and rest <= nat)


def _get_events(sport: str, day: date) -> list[dict]:
    espn_sport, espn_league = SPORT_ENDPOINTS[sport]
    url = f"{BASE_URL}/{espn_sport}/{espn_league}/scoreboard"
    for attempt in range(4):
        try:
            resp = requests.get(url, params={"dates": day.strftime("%Y%m%d")}, timeout=30)
            resp.raise_for_status()
            return resp.json().get("events", [])
        except requests.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"ESPN request failed for {sport} {day}: {exc}") from exc
            time.sleep(2 ** attempt)
    return []


def parse_broadcasts(event: dict) -> dict:
    """Returns {"national": [...names], "all": [...names]} for one ESPN event.

    Prefers `geoBroadcasts` (has market type, media name, region) and falls
    back to the simpler `broadcasts` list (market + names) when it's absent.
    """
    comp = (event.get("competitions") or [{}])[0]
    national, every = [], []

    geo = comp.get("geoBroadcasts") or []
    if geo:
        for g in geo:
            name = ((g.get("media") or {}).get("shortName") or "").strip()
            market = ((g.get("market") or {}).get("type") or "").lower()
            region = (g.get("region") or "us").lower()
            if not name:
                continue
            every.append(name)
            if market == "national" and region == "us":
                national.append(name)
    else:
        for b in comp.get("broadcasts") or []:
            names = [n.strip() for n in b.get("names") or [] if n and n.strip()]
            every += names
            if (b.get("market") or "").lower() == "national":
                national += names

    dedupe = lambda xs: sorted(set(xs), key=xs.index)
    return {"national": dedupe(national), "all": dedupe(every)}


def wanted_games() -> pd.DataFrame:
    frames = []
    for sport in SPORTS:
        d = pd.read_csv(ROOT / "data" / f"{sport}_{SEASON}_historical.csv",
                        usecols=["game_id", "start_time_utc"], dtype={"game_id": str})
        d["sport"] = sport
        frames.append(d)
    games = pd.concat(frames, ignore_index=True)
    games["start_time_utc"] = pd.to_datetime(games["start_time_utc"], utc=True)
    return games


def main():
    games = wanted_games()
    done: dict[tuple[str, str], list[dict]] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done[(rec["sport"], rec["date"])] = rec["events"]

    found: dict[str, dict] = {}
    for sport in SPORTS:
        g = games[games["sport"] == sport]
        ids = set(g["game_id"])
        # ESPN files games under their US-local date; a late game can sit on
        # the next UTC date, so request both the ET and UTC dates of every game.
        days = set(g["start_time_utc"].dt.tz_convert("America/New_York").dt.date) | set(g["start_time_utc"].dt.date)
        days = sorted(days)
        todo = [d for d in days if (sport, d.isoformat()) not in done]
        print(f"{sport}: {len(ids)} games on {len(days)} dates ({len(todo)} to fetch)")

        with open(CHECKPOINT, "a", encoding="utf-8") as ckpt:
            for i, day in enumerate(todo, 1):
                events = [
                    {"game_id": str(e.get("id")), **parse_broadcasts(e)}
                    for e in _get_events(sport, day) if str(e.get("id")) in ids
                ]
                done[(sport, day.isoformat())] = events
                ckpt.write(json.dumps({"sport": sport, "date": day.isoformat(), "events": events}) + "\n")
                ckpt.flush()
                if i % 50 == 0:
                    print(f"  {i}/{len(todo)} dates")
                time.sleep(REQUEST_DELAY)

        for day in days:
            for ev in done.get((sport, day.isoformat()), []):
                found[ev["game_id"]] = {"sport": sport, **ev}

    rows = [{"game_id": gid, "sport": v["sport"], "national_tv": is_national_exclusive(v["national"], v["all"]),
             "national_networks": "|".join(v["national"]), "all_networks": "|".join(v["all"])}
            for gid, v in found.items()]
    out = pd.DataFrame(rows, columns=["game_id", "sport", "national_tv", "national_networks", "all_networks"])
    out.to_csv(OUT_PATH, index=False)

    missing = set(games["game_id"]) - set(out["game_id"])
    print(f"\nWrote {OUT_PATH.relative_to(ROOT)}: {len(out)} games, {len(missing)} not found on ESPN")
    print("\nShare of games with national TV:")
    print(out.groupby("sport")["national_tv"].agg(["mean", "size"]).round(3).to_string())
    print("\nNetworks on games counted as national exclusives (check this list):")
    nets = out[out["national_tv"] == 1]
    nets = nets.assign(net=nets["national_networks"].str.split("|")).explode("net")
    nets = nets[nets["net"].fillna("") != ""]
    for sport, s in nets.groupby("sport")["net"]:
        print(f"  {sport}: " + ", ".join(f"{k} ({v})" for k, v in s.value_counts().items()))


if __name__ == "__main__":
    main()
