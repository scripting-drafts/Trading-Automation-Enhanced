import time
import yaml
from datetime import datetime
from statistics import stdev
from binance.client import Client
from pycoingecko import CoinGeckoAPI
from secret import API_KEY, API_SECRET

OUTPUT_FILE = "symbols.yaml"
UPDATE_INTERVAL = 300  # <-- in seconds (e.g., 300 = 5 minutes)
cg = CoinGeckoAPI()

def fetch_with_retry(func, *args, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"[WARN] Attempt {attempt+1} failed: {e}")
            time.sleep(2)
    print("[ERROR] All retries failed.")
    return None

def build_coingecko_mapping(binance_base_assets):
    all_coins = fetch_with_retry(cg.get_coins_list) or []
    symbol_map = {c['symbol'].upper(): c['id'] for c in all_coins}
    mapping = {}
    for asset in binance_base_assets:
        cg_id = symbol_map.get(asset.upper())
        if cg_id:
            mapping[asset.upper()] = cg_id
    return mapping

def fetch_cg_marketcaps_and_supply(cg_ids):
    data = {}
    batch_size = 250
    for i in range(0, len(cg_ids), batch_size):
        sublist = cg_ids[i:i + batch_size]
        resp = fetch_with_retry(cg.get_coins_markets, vs_currency='usd', ids=','.join(sublist)) or []
        for coin in resp:
            symbol_uc = coin['symbol'].upper()
            data[symbol_uc] = {
                "market_cap": coin.get('market_cap'),
                "circulating_supply": coin.get('circulating_supply')
            }
    return data

def calc_volatility(closes):
    if len(closes) < 2:
        return 0.0
    returns = [(closes[i]/closes[i-1]) - 1 for i in range(1, len(closes))]
    return float(stdev(returns)) if len(returns) > 1 else 0.0

def fetch_symbol_data(client, symbol_info, cg_entry):
    symbol = symbol_info['symbol']
    try:
        ticker = fetch_with_retry(client.get_ticker, symbol=symbol)
        if not ticker:
            print(f"[ERROR] Failed to fetch ticker for {symbol}")
            return None
        last_price = float(ticker['lastPrice'])

        market_cap = cg_entry.get("market_cap")
        circulating_supply = cg_entry.get("circulating_supply")

        closes_15m = [float(k[4]) for k in fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=15) or []]
        closes_1h  = [float(k[4]) for k in fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=60) or []]
        closes_1d  = [float(k[4]) for k in fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=96) or []]

        klines_15m = fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=1) or []
        klines_1h = fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_1HOUR, limit=1) or []
        klines_1d = fetch_with_retry(client.get_klines, symbol=symbol, interval=Client.KLINE_INTERVAL_1DAY, limit=1) or []
        depth = fetch_with_retry(client.get_order_book, symbol=symbol, limit=5)
        if not depth or 'bids' not in depth or 'asks' not in depth:
            print(f"[ERROR] Failed to fetch depth for {symbol}")
            return None

        return {
            "market_cap": market_cap,
            "circulating_supply": circulating_supply,
            "last_price": last_price,
            "volatility": {
                "15m": calc_volatility(closes_15m),
                "1h": calc_volatility(closes_1h),
                "1d": calc_volatility(closes_1d)
            },
            "volume_15m": float(klines_15m[0][5]) if klines_15m else 0,
            "volume_1h": float(klines_1h[0][5]) if klines_1h else 0,
            "volume_1d": float(klines_1d[0][5]) if klines_1d else 0,
            "arbitrage": {
                "bid_ask_spread": float(depth['asks'][0][0]) - float(depth['bids'][0][0]),
                "top_bid": float(depth['bids'][0][0]),
                "top_ask": float(depth['asks'][0][0]),
                "order_book_depth": sum(float(x[1]) for x in depth['bids']) + sum(float(x[1]) for x in depth['asks'])
            }
        }
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return None

def update_btc_eth_pairs(client):
    target_symbols = ['BTCUSDT', 'ETHUSDT']
    data = {}
    print(f"\n[{datetime.now()}] Fetching BTCUSDT / ETHUSDT ...")

    for symbol in target_symbols:
        base_asset = symbol.replace("USDT", "")
        cg_mapping = build_coingecko_mapping([base_asset])
        cg_entry = {}
        if cg_mapping:
            cg_id = cg_mapping.get(base_asset.upper())
            if cg_id:
                cg_entry = fetch_cg_marketcaps_and_supply([cg_id]).get(base_asset.upper(), {})
        symbol_info = {
            'symbol': symbol,
            'baseAsset': base_asset,
            'quoteAsset': 'USDT',
            'status': 'TRADING'
        }
        symbol_data = fetch_symbol_data(client, symbol_info, cg_entry)
        if symbol_data:
            data[symbol] = symbol_data
        time.sleep(0.25)

    try:
        existing = {}
        try:
            with open(OUTPUT_FILE, "r") as f:
                existing = yaml.safe_load(f) or {}
        except FileNotFoundError:
            pass

        existing.update(data)
        with open(OUTPUT_FILE, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
        print(f"[{datetime.now()}] Updated {', '.join(data.keys())} in {OUTPUT_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to update YAML: {e}")

if __name__ == "__main__":
    client = Client(API_KEY, API_SECRET, requests_params={'timeout': 10})
    while True:
        update_btc_eth_pairs(client)
        print(f"[INFO] Sleeping for {UPDATE_INTERVAL} seconds...\n")
        time.sleep(UPDATE_INTERVAL)
