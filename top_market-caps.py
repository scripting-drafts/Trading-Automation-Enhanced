import yaml

with open("symbols.yaml", "r") as f:
    data = yaml.safe_load(f)

# Extract symbols with market_cap
symbols_with_mc = [
    (symbol, info.get("market_cap", 0))
    for symbol, info in data.items()
    if info.get("market_cap") is not None
]

# Sort by market_cap descending
top_10 = sorted(symbols_with_mc, key=lambda x: x[1], reverse=True)[:10]

for symbol, market_cap in top_10:
    print(f"{symbol}: {market_cap:,}")