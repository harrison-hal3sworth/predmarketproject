# Kalshi vs. Sportsbook: Market Efficiency in Sports Prediction Markets

MATH 540 research project. Compares Kalshi prediction-market prices with bet365
closing moneylines and game outcomes for the 2025 MLB season and the 2025–26
NFL, NBA and NHL seasons, to ask:

1. Is there a profitable trading strategy that exploits differences between the two?
2. How do liquidity and trading volume relate to the size of those differences?
3. Are these sports prediction markets significantly inefficient?

Findings, tests and caveats are in `analysis/models_complete.ipynb` (summary at the end).
Two exploratory follow-ups, specified before running, extend the original questions:
price leadership between the venues (section 8) and a multiple-testing-corrected
search for the least efficient types of game (section 9).

## Repository layout

| Path | What it is |
|---|---|
| `config/config.yaml` | Per-sport settings (Kalshi series, OddsHarvester sport/league/market) and the active sport + date range for a pipeline run |
| `scripts/build_historical_dataset.py` | Main pipeline entry point: schedule → Kalshi markets → sportsbook odds → joined CSV |
| `src/fetchers/` | `schedule.py` (ESPN scoreboard), `kalshi_history.py` (Kalshi historical API), `sportsbook_history.py` (OddsHarvester wrapper) |
| `src/matching/event_matcher.py` | Matches Kalshi tickers to scheduled games |
| `src/pipeline/historical_snapshot.py` | Joins everything into one row per game |
| `src/pricing/devig.py` | Odds → probability and de-vig methods (tested in `tests/`) |
| `data/lookup_tables/` | Kalshi team code → ESPN team name maps (`*.ignored.json` documents deliberate exclusions) |
| `data/{sport}_2025_historical.csv` | Raw joined pipeline output, one file per sport |
| `data/raw_oddsharvester_*.json` | Raw OddsHarvester scrapes (`.complete` marks a finished scrape; `.checkpoint.ndjson` is the crash-recovery stream) |
| `cleaning/cleaning.ipynb` | Cleaning + EDA → `data/clean_data.csv` |
| `analysis/build_model_table.py` | Builds `data/model_table.csv`, the modeling dataset (column glossary at the top of the script) |
| `analysis/models_complete.ipynb` | All models, robustness checks and the strategy backtest |

Optional data add-ons (each one is a standalone fetch, merged automatically by
`build_model_table.py` when its output file exists):

| Script | Output | Used for |
|---|---|---|
| `scripts/fetch_kalshi_early_prices.py` | `data/kalshi_early_prices.csv` | Weak-form test (notebook section 4): Kalshi quotes 24, 6 and 2 hours before kickoff, from the tickers already in the CSVs. About 9,700 API requests (1-minute candles), 20–30 minutes, resumable. Run with `--probe` first. |
| `scripts/fetch_espn_broadcasts.py` | `data/espn_broadcasts.csv` | `national_tv` indicator (national exclusive: no local/regional broadcast listed) in the liquidity model (section 5). About 700 ESPN requests, a few minutes, resumable. |

Helper scripts: `verify_oddsharvester_sport.py` (check a sport/league/market against
the installed OddsHarvester), `extract_team_codes.py` / `verify_team_names.py`
(build and check team maps), `recover_checkpoint.py` (rebuild a scrape from its
checkpoint after a crash), `diagnose_kalshi_candles.py` (check Kalshi's candlestick
endpoints for one known market if a candle fetch comes back empty).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; source .venv/bin/activate elsewhere
pip install -r requirements.txt
pip install oddsharvester         # only needed to re-scrape sportsbook odds
playwright install chromium
```

Kalshi's historical endpoints and ESPN's scoreboard are public, so the pipeline
needs no API keys. `secrets/` (gitignored) holds credentials used only by the
early live-API `scripts/smoke_test.py`.

## Reproducing the results

**1. Pull data (one sport at a time).** In `config/config.yaml`, uncomment the
`active_sport` / `date_range` / `output_csv` block for the sport, then:

```bash
python scripts/verify_oddsharvester_sport.py --from-config   # optional sanity check
python scripts/build_historical_dataset.py
```

If `data/raw_oddsharvester_{sport}.json.complete` exists, the slow sportsbook
scrape is skipped and the existing raw file is reused. Delete the marker to re-scrape.

**2. (Optional) Fetch the add-on data.** Neither touches the sportsbook scrape.

```bash
python scripts/fetch_kalshi_early_prices.py
python scripts/fetch_espn_broadcasts.py
```

**3. Build the modeling table.**

```bash
python analysis/build_model_table.py
```

**4. Run the notebooks** from their own folders: `cleaning/cleaning.ipynb` (EDA),
then `analysis/models_complete.ipynb` (results).

**Tests:** `pytest`

## Dataset

`data/model_table.csv`: **4,865 games × 61 columns** (47 core columns, plus 14 from
the two add-on files above), one row per game from the
home team's perspective, Apr 2025 – Jun 2026.

| Sport | Games |
|---|---|
| MLB | 1,991 |
| NHL | 1,384 |
| NBA | 1,214 |
| NFL | 276 |

The raw pipeline output is 5,160 games. 295 are dropped for missing prices or
odds, missing results or ties, or empty Kalshi books.

- **Kalshi price:** bid/ask/close from the last candle within 30 minutes before kickoff.
- **Sportsbook price:** bet365 closing moneyline as listed on OddsPortal. The time
  of bet365's final update is not recorded, so the two prices may not be simultaneous.
- **Volume and open interest** are in contracts. From mid-April 2026 Kalshi allowed
  fractional contracts, so later NBA/NHL values are non-integer; the scale is unchanged.
- **De-vig:** Cheung (odds-ratio) is the primary method, fixed in advance; the others
  are reported as sensitivity checks.

## Known limitations

- One season per sport and one sportsbook.
- Closing quotes only: no order-book depth.
- Kalshi had only MLB markets before September 2025, so platform age is
  confounded with sport.
