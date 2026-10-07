raw = {s: read_season_data_file(s, 2025) for s in SPORTS_SEASONS}

for s, df in raw.items():
    v = df["kalshi_home_volume"] + df["kalshi_away_volume"]
    print(s, v.describe(percentiles=[.01, .05, .25, .5]).round(0).to_dict())
    print("  combined volume < 100:", (v < 100).sum(), "of", v.notna().sum())