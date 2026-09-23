import requests, json

BASE = "https://api.elections.kalshi.com/trade-api/v2"

r = requests.get(f"{BASE}/historical/markets",
                  params={"series_ticker": "KXNFLGAME", "status": "settled", "limit": 200})
markets = r.json()["markets"]
print(f"Got {len(markets)} markets.")

markets_sorted = sorted(markets, key=lambda m: m.get("volume_fp", 0), reverse=True)
top = markets_sorted[0]
print("Highest-volume market:", top["ticker"], "| volume_fp:", top.get("volume_fp"),
      "| open_time:", top.get("open_time"), "| close_time:", top.get("close_time"))

ticker = top["ticker"]
r2 = requests.get(f"{BASE}/historical/markets/{ticker}/candlesticks",
                   params={"start_ts": 1609459200, "end_ts": 1790000000, "period_interval": 1440})
candles = r2.json().get("candlesticks", [])
print(f"\n{len(candles)} candles found")
if candles:
    print(json.dumps(candles[0], indent=2))
else:
    print("Still empty — raw response:", json.dumps(r2.json(), indent=2)[:1000])