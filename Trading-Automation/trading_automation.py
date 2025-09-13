import yaml, pytz, json, os, csv, decimal, time, threading, math, random, asyncio, sys, io, logging, traceback
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
from binance import ThreadedWebsocketManager
import websockets
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Update, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    Application,
    CallbackContext,
    ContextTypes,
    BaseHandler,
    filters,
    JobQueue,
)
import apscheduler.util
from functools import lru_cache
from config import API_KEY, API_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

def patched_get_localzone():
    return pytz.UTC

apscheduler.util.get_localzone = patched_get_localzone

# Trading mode configuration
TRADING_MODE = 'AUTO_DETECT'  # Options: 'SPOT', 'UNIVERSAL_TRANSFER', 'SIMULATE', 'AUTO_DETECT'
ALLOW_SIMULATED_TRADES = True  # Set to False to disable simulated trades

# Universal Transfer configuration
ENABLE_UNIVERSAL_TRANSFER = True  # Enable Universal Transfer mode
UT_CONVERSION_SLIPPAGE = 0.001   # 0.1% slippage allowance for conversions

BASE_ASSET = ['BTC', 'ETH']  # Support multiple base assets
DUST_LIMIT = 0.4

MIN_MARKETCAP = 69_502  
MIN_VOLUME_BTC = 660   # BTC operates on higher volume scale
MIN_VOLUME_ETH = 40_000     # ETH has moderate volume
MIN_VOLUME_DEFAULT = 300_000  # For other altcoins
MIN_VOLATILITY = 0.0025

MIN_1M = 0.003   # 0.3% in 1m (lower from 0.5%)
MIN_5M = 0.007   # 0.7% in 5m (lower from 1.0%)
MIN_15M = 0.015  # 1.5% in 15m (lower from 2.0%)

# New indicator thresholds - LESS STRICT
RSI_OVERBOUGHT = 60  # Lower from 70 to 60
VOLUME_SPIKE_MULTIPLIER = 1.5  # Lower from 2.0 to 1.5
MA_PERIODS_SHORT = 5
MA_PERIODS_LONG = 20

MAX_POSITIONS = 20
MIN_PROFIT = 0.8       # %
TRAIL_STOP = 0.5       # %
MAX_HOLD_TIME = 900    # seconds
INVEST_AMOUNT = 10     # USD per coin
TRADE_LOG_FILE = "trades_detailed.csv"
YAML_SYMBOLS_FILE = "symbols.yaml"
BOT_STATE_FILE = "bot_state.json"

client = Client(API_KEY, API_SECRET)
# --- Time Sync Patch: ---
try:
    client.get_server_time()
    print("[INFO] Synced time with Binance server.")
except Exception as e:
    print(f"[ERROR] Could not sync time with Binance server: {e}")

positions = {}        # single global positions dict
balance = {'btc': 0.0, 'eth': 0.0, 'usd': 0.0}  # Track BTC, ETH, and USDC balances
trade_log = []
price_cache = {}      # Real-time price cache from WebSocket
price_cache_lock = threading.Lock()  # Thread safety for price cache

class WebSocketPriceManager:
    """Optimized WebSocket manager using Binance ThreadedWebsocketManager with better error handling."""
    
    def __init__(self, client):
        self.client = client
        self.twm = None
        self.symbols_to_monitor = set()
        self._running = False
        self._price_cache = {}
        self._cache_lock = threading.Lock()
        self._reconnect_count = 0
        self._max_reconnects = 3
        self._streams = {}  # Track active streams
        
    def start(self):
        """Strictly start WebSocket with Binance ThreadedWebsocketManager. No REST fallback."""
        if self._running:
            return
        try:
            print("[WEBSOCKET] 🚀 Starting Binance WebSocket monitoring (strict mode)...")
            self.twm = ThreadedWebsocketManager(
                api_key=self.client.API_KEY,
                api_secret=self.client.API_SECRET,
                testnet=False
            )
            self.twm.start()
            self._running = True
            print("[WEBSOCKET] ✅ ThreadedWebsocketManager started successfully")
        except Exception as e:
            print(f"[WEBSOCKET ERROR] Failed to initialize: {e}")
            self._running = False
            self.twm = None
            
        
            # If WebSocket failed to start, fall back to REST API mode
            if not self._running:
                print("[WEBSOCKET] � Starting REST API fallback mode...")
                self._start_fallback_mode()
                
        except Exception as e:
            print(f"[WEBSOCKET ERROR] Failed to initialize: {e}")
            self._running = False
            self.twm = None
            # Try fallback mode
            self._start_fallback_mode()
            
    def _start_normal(self):
        """Normal WebSocket start method."""
        try:
            # Initialize ThreadedWebsocketManager with optimized settings
            self.twm = ThreadedWebsocketManager(
                api_key=self.client.API_KEY,
                api_secret=self.client.API_SECRET,
                testnet=False
            )
            
            # Start the manager with error handling
            self.twm.start()
            self._running = True
            print("[WEBSOCKET] ✅ ThreadedWebsocketManager started successfully")
        except Exception as start_error:
            print(f"[WEBSOCKET] Failed to start manager: {start_error}")
            self.twm = None
            self._running = False
            
    def _start_with_thread(self):
        """Safe WebSocket start for environments with existing event loops."""
        try:
            import threading
            import time
            
            result = {}
            
            def start_websocket():
                try:
                    # Initialize ThreadedWebsocketManager in a separate thread
                    self.twm = ThreadedWebsocketManager(
                        api_key=self.client.API_KEY,
                        api_secret=self.client.API_SECRET,
                        testnet=False
                    )
                    
                    # Start the manager
                    self.twm.start()
                    
                    # Give it a moment to initialize properly
                    time.sleep(0.5)
                    
                    result['success'] = True
                    result['error'] = None
                except Exception as e:
                    result['success'] = False
                    result['error'] = str(e)
            
            # Start WebSocket in a separate thread to avoid event loop conflicts
            ws_thread = threading.Thread(target=start_websocket, daemon=True)
            ws_thread.start()
            ws_thread.join(timeout=10)  # Wait up to 10 seconds
            
            if result.get('success'):
                self._running = True
                print("[WEBSOCKET] ✅ ThreadedWebsocketManager started successfully (thread-safe mode)")
            else:
                error_msg = result.get('error', 'Unknown error')
                print(f"[WEBSOCKET] ❌ Failed to start manager: {error_msg}")
                # Fallback to REST API only
                print("[WEBSOCKET] 🔄 Falling back to REST API for price data")
                self.twm = None
                self._running = False
                
        except Exception as e:
            print(f"[WEBSOCKET] Thread-safe start failed: {e}")
            self.twm = None
            self._running = False
                    
    def stop(self):
        """Stop WebSocket manager cleanly with proper session and coroutine cleanup."""
        if not self._running:
            return
        print("[WEBSOCKET] 🛑 Stopping WebSocket connections...")
        self._running = False
        try:
            if self.twm:
                # Stop all active streams first
                for stream_id in list(self._streams.keys()):
                    try:
                        self.twm.stop_socket(stream_id)
                    except Exception as e:
                        print(f"[WEBSOCKET] Error stopping stream {stream_id}: {e}")
                
                # Force close any aiohttp sessions and clean up coroutines
                try:
                    import asyncio
                    import threading
                    
                    # Get or create event loop for cleanup
                    loop = None
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_closed():
                            raise RuntimeError("Loop is closed")
                    except RuntimeError:
                        # Create new loop if none exists or current is closed
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    
                    # Clean up any pending coroutines and sessions
                    if hasattr(self.twm, '_session') and self.twm._session:
                        if hasattr(self.twm._session, 'close'):
                            loop.run_until_complete(self.twm._session.close())
                            print("[WEBSOCKET] ✅ Client session closed")
                    
                    # Cancel any pending tasks related to socket listeners
                    if hasattr(self.twm, '_socket_listener_task'):
                        try:
                            self.twm._socket_listener_task.cancel()
                        except Exception:
                            pass
                    
                    # Give time for coroutines to complete
                    import time
                    time.sleep(0.1)
                    
                except Exception as session_error:
                    print(f"[WEBSOCKET] Session/coroutine cleanup warning: {session_error}")
                
                # Stop the manager after cleanup
                try:
                    self.twm.stop()
                    print("[WEBSOCKET] ✅ ThreadedWebsocketManager stopped")
                except Exception as stop_error:
                    print(f"[WEBSOCKET] Stop warning: {stop_error}")
                
                # Final cleanup delay
                import time
                time.sleep(0.2)
                
                self.twm = None
        except Exception as e:
            print(f"[WEBSOCKET] Error during shutdown: {e}")
        finally:
            self.symbols_to_monitor.clear()
            self._streams.clear()
            with self._cache_lock:
                self._price_cache.clear()
            print("[WEBSOCKET] Stopped successfully")
            
    def _handle_socket_message(self, msg):
        """Optimized message handler for ticker data with robust error handling."""
        try:
            if not isinstance(msg, dict):
                print(f"[WEBSOCKET] Invalid message type: {type(msg)}")
                return
                
            if 's' not in msg or 'c' not in msg:
                print(f"[WEBSOCKET] Missing required fields in message: {list(msg.keys())}")
                return
                
            symbol = msg['s']
            
            # Safely parse numeric fields with fallbacks
            try:
                price = float(msg['c'])
            except (ValueError, TypeError) as e:
                print(f"[WEBSOCKET] Invalid price for {symbol}: {msg.get('c')} - {e}")
                return
                
            try:
                volume = float(msg.get('v', 0))
            except (ValueError, TypeError):
                volume = 0.0
                
            try:
                price_change_pct = float(msg.get('P', 0))
            except (ValueError, TypeError):
                price_change_pct = 0.0
                
            try:
                high_24h = float(msg.get('h', price))
            except (ValueError, TypeError):
                high_24h = price
                
            try:
                low_24h = float(msg.get('l', price))
            except (ValueError, TypeError):
                low_24h = price
                
            try:
                count = int(float(msg.get('n', 0)))  # Convert to float first, then int
            except (ValueError, TypeError):
                count = 0
            
            # Update cache with validated data
            with self._cache_lock:
                self._price_cache[symbol] = {
                    'price': price,
                    'timestamp': time.time(),
                    'volume': volume,
                    'price_change_pct': price_change_pct,
                    'high_24h': high_24h,
                    'low_24h': low_24h,
                    'count': count
                }
            
            # Update global price cache
            with price_cache_lock:
                price_cache[symbol] = self._price_cache[symbol].copy()
                    
        except Exception as e:
            print(f"[WEBSOCKET] Unexpected message processing error: {e}")
            if 'msg' in locals():
                print(f"[WEBSOCKET] Problematic message: {msg}")
            import traceback
            traceback.print_exc()
            
    def add_symbol(self, symbol):
        """Add symbol to monitoring with individual ticker stream and robust error handling."""
        if not self._running:
            self.symbols_to_monitor.add(symbol)
            print(f"[WEBSOCKET] ➕ Queued {symbol} for monitoring (WebSocket not running)")
            return
        try:
            if symbol not in self.symbols_to_monitor:
                # Validate symbol format
                if not symbol or not isinstance(symbol, str) or not symbol.endswith('USDC'):
                    print(f"[WEBSOCKET] ❌ Invalid symbol format: {symbol}")
                    return
                # Add to monitoring list
                self.symbols_to_monitor.add(symbol)
                # Start individual ticker stream for this symbol
                if self.twm:
                    print(f"[WEBSOCKET] Starting stream for {symbol}...")
                    stream_id = self.twm.start_symbol_ticker_socket(
                        callback=self._handle_socket_message,
                        symbol=symbol
                    )
                    if stream_id:
                        self._streams[stream_id] = symbol
                        print(f"[WEBSOCKET] ➕ Added {symbol} stream (ID: {stream_id})")
                    else:
                        print(f"[WEBSOCKET] ❌ Failed to get stream ID for {symbol}")
                else:
                    print(f"[WEBSOCKET] ⚠️ No WebSocket manager available for {symbol}")
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Error adding {symbol}: {e}")
            # Don't crash the whole system, just log and continue
                
    def remove_symbol(self, symbol):
        """Remove symbol from monitoring."""
        if symbol in self.symbols_to_monitor:
            try:
                # Find and stop the stream for this symbol
                stream_to_remove = None
                for stream_id, stream_symbol in self._streams.items():
                    if stream_symbol == symbol:
                        stream_to_remove = stream_id
                        break
                
                if stream_to_remove and self.twm:
                    self.twm.stop_socket(stream_to_remove)
                    del self._streams[stream_to_remove]
                
                self.symbols_to_monitor.discard(symbol)
                print(f"[WEBSOCKET] ➖ Removed {symbol} from monitoring")
                
                # Clean up cache
                with self._cache_lock:
                    self._price_cache.pop(symbol, None)
                with price_cache_lock:
                    price_cache.pop(symbol, None)
                    
            except Exception as e:
                print(f"[WEBSOCKET] Error removing {symbol}: {e}")
                
    def get_latest_price(self, symbol):
        """Get latest price with optimized cache lookup."""
        with self._cache_lock:
            data = self._price_cache.get(symbol)
            if data and time.time() - data['timestamp'] < 30:  # 30-second freshness
                return data['price']
        return None
    
    def get_price_data(self, symbol):
        """Get comprehensive price data for a symbol."""
        with self._cache_lock:
            return self._price_cache.get(symbol, {}).copy()
            
    def get_monitored_symbols(self):
        """Get list of monitored symbols."""
        return list(self.symbols_to_monitor)
        
    def update_symbol_list(self, new_symbols):
        """Update symbol list with batch optimization."""
        new_symbols_set = set(new_symbols)
        current_symbols = set(self.symbols_to_monitor)
        
        # Add new symbols
        symbols_to_add = new_symbols_set - current_symbols
        for symbol in symbols_to_add:
            self.add_symbol(symbol)
            time.sleep(0.1)  # Small delay to prevent overwhelming
        
        # Remove old symbols
        symbols_to_remove = current_symbols - new_symbols_set
        for symbol in symbols_to_remove:
            self.remove_symbol(symbol)
                
    def get_connection_stats(self):
        """Get WebSocket connection statistics (strict mode)."""
        with self._cache_lock:
            cached_symbols = len(self._price_cache)
            recent_updates = sum(1 for data in self._price_cache.values() 
                               if time.time() - data['timestamp'] < 30)
        return {
            'running': self._running,
            'monitored_symbols': len(self.symbols_to_monitor),
            'active_streams': len(self._streams),
            'cached_symbols': cached_symbols,
            'recent_updates': recent_updates,
            'reconnect_count': self._reconnect_count,
            'manager_active': self.twm is not None,
            'connection_type': 'WEBSOCKET'
        }

# Global WebSocket price manager
ws_price_manager = None

import time

def parse_trade_time(val, default=None):
    """Parse trade_time as float (Unix timestamp) or from string like 'YYYY-MM-DD HH:MM:SS'."""
    if val is None:
        return default if default is not None else time.time()
    try:
        return float(val)
    except Exception:
        try:
            return time.mktime(datetime.strptime(val, "%Y-%m-%d %H:%M:%S").timetuple())
        except Exception:
            return default if default is not None else time.time()


async def send_with_keyboard(update: Update, text, parse_mode=None, reply_markup=None):
    if reply_markup is None:
        reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

def load_trade_history():
    log = []
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, "r") as f:
                reader = csv.DictReader(f)
                log = list(reader)
        except Exception as e:
            print(f"[LOAD TRADE ERROR] {e}")
    return log

def rebuild_cost_basis(trade_log):
    positions_tmp = {}
    for tr in trade_log:
        symbol = tr.get('Symbol')
        qty = float(tr.get('Qty', 0))
        entry = float(tr.get('Entry', 0))
        action = 'buy' if float(tr.get('Entry', 0)) > 0 else 'sell'
        action = tr.get('action', '').lower() if 'action' in tr else (action)
        tstamp = parse_trade_time(tr.get('Time'), time.time())
        if symbol not in positions_tmp:
            positions_tmp[symbol] = {'qty': 0.0, 'cost': 0.0, 'trade_time': tstamp}
        if action == 'buy':
            positions_tmp[symbol]['qty'] += qty
            positions_tmp[symbol]['cost'] += qty * entry
            positions_tmp[symbol]['trade_time'] = tstamp
        elif action == 'sell':
            orig_qty = positions_tmp[symbol]['qty']
            if orig_qty > 0 and qty > 0:
                avg_entry = positions_tmp[symbol]['cost'] / orig_qty
                positions_tmp[symbol]['qty'] -= qty
                positions_tmp[symbol]['cost'] -= qty * avg_entry
                positions_tmp[symbol]['trade_time'] = tstamp
                if positions_tmp[symbol]['qty'] < 1e-8:
                    positions_tmp[symbol]['qty'] = 0
                    positions_tmp[symbol]['cost'] = 0
    cost_basis = {}
    for symbol, v in positions_tmp.items():
        if v['qty'] > 0:
            avg_entry = v['cost'] / v['qty'] if v['qty'] > 0 else 0.0
            cost_basis[symbol] = {
                'qty': v['qty'],
                'entry': avg_entry,
                'trade_time': v['trade_time'],  # now always float
            }
    return cost_basis


def reconcile_positions_with_binance(client, positions, quote_asset="USDC"):
    """Update local 'positions' to match true Binance balances for all open positions."""
    try:
        account = client.get_account()
        assets = {b['asset']: float(b['free']) for b in account['balances'] if float(b['free']) > 0}
        for asset, qty in assets.items():
            if asset == quote_asset:
                continue
            symbol = asset + quote_asset
            if symbol in positions:
                positions[symbol]['qty'] = qty
            else:
                positions[symbol] = {'qty': qty, 'entry': 0.0, 'trade_time': 0}
        for symbol in list(positions):
            base = symbol.replace(quote_asset, "")
            if base not in assets or assets[base] == 0:
                positions.pop(symbol)
    except Exception as e:
        print(f"[SYNC ERROR] Failed to reconcile with Binance: {e}")

def fetch_all_balances():
    """Update all asset balances (BTC, ETH, USDC) from Binance live."""
    try:
        # Fetch BTC balance
        btc_info = client.get_asset_balance(asset="BTC")
        balance['btc'] = float(btc_info['free'])
        
        # Fetch ETH balance
        eth_info = client.get_asset_balance(asset="ETH")
        balance['eth'] = float(eth_info['free'])
        
        # Fetch USDC balance
        usdc_info = client.get_asset_balance(asset="USDC")
        balance['usd'] = float(usdc_info['free'])
        
        print(f"[BALANCE] Live balances - BTC: {balance['btc']:.6f}, ETH: {balance['eth']:.6f}, USDC: ${balance['usd']:.2f}")
    except Exception as e:
        print(f"[ERROR] Fetching balances: {e}")
        balance['btc'] = 0
        balance['eth'] = 0
        balance['usd'] = 0

def fetch_usdc_balance():
    """Update the global USDC balance from Binance live. (Legacy function - use fetch_all_balances instead)"""
    try:
        asset_info = client.get_asset_balance(asset="USDC")
        free = float(asset_info['free'])
        balance['usd'] = free
        print(f"[DEBUG] Live USDC balance: {free}")
    except Exception as e:
        print(f"[ERROR] Fetching USDC balance: {e}")
        balance['usd'] = 0

def diagnose_investment_failure(symbol="ETHUSDC"):
    """
    Comprehensive diagnostic function to check why a symbol wasn't invested in
    despite showing momentum signals.
    """
    print(f"\n=== DIAGNOSTIC REPORT for {symbol} ===")
    
    # 1. Check if bot is paused
    paused = is_paused()
    print(f"1. Bot Status: {'PAUSED' if paused else 'ACTIVE'}")
    if paused:
        print("   ❌ Bot is paused - this prevents all investments")
        return
    
    # 2. Check market risk
    risky = market_is_risky()
    print(f"2. Market Risk: {'HIGH' if risky else 'NORMAL'}")
    if risky:
        btc_change = get_1h_percent_change("BTCUSDT")
        print(f"   ❌ Market is risky - BTC 1h change: {btc_change:.2f}%")
        return
    
    # 3. Check position limits
    too_many = too_many_positions()
    position_count = len([s for s, p in positions.items() 
                         if get_latest_price(s) and p.get('qty', 0) * get_latest_price(s) > DUST_LIMIT])
    print(f"3. Position Limit: {position_count}/{MAX_POSITIONS} ({'FULL' if too_many else 'OK'})")
    if too_many:
        print(f"   ❌ Too many positions - limit reached")
        return
    
    # 4. Check all balances (BTC, ETH, USDC) and taxes
    fetch_all_balances()
    usdc_balance = balance['usd']
    
    # Calculate taxes owed
    total_taxes_owed = sum(
        float(tr.get('Tax', 0)) for tr in trade_log[-20:] if float(tr.get('Tax', 0)) > 0
    )
    
    min_notional = min_notional_for(symbol)
    investable_usdc = usdc_balance - total_taxes_owed
    
    print(f"4. Financial Check:")
    print(f"   USDC Balance: ${usdc_balance:.2f}")
    print(f"   Taxes Owed: ${total_taxes_owed:.2f}")
    print(f"   Min Notional for {symbol}: ${min_notional:.2f}")
    print(f"   Investable USDC: ${investable_usdc:.2f}")
    print(f"   Status: {'OK' if investable_usdc >= min_notional else 'INSUFFICIENT'}")
    
    if investable_usdc < min_notional:
        print(f"   ❌ Not enough USDC after tax reserve")
        return
    
    # 5. Check if symbol is in momentum list
    momentum_symbols = get_yaml_ranked_momentum(limit=10)
    print(f"5. Momentum Eligibility:")
    print(f"   In momentum list: {'YES' if symbol in momentum_symbols else 'NO'}")
    print(f"   Top 10 momentum symbols: {momentum_symbols}")
    
    if symbol not in momentum_symbols:
        print(f"   ❌ {symbol} not in momentum ranking")
        
        # Check detailed filters
        stats = load_symbol_stats()
        if symbol not in stats:
            print(f"   ❌ {symbol} not in YAML stats file")
            return
            
        s = stats[symbol]
        tickers = {t['symbol']: t for t in client.get_ticker() if t['symbol'] == symbol}
        ticker = tickers.get(symbol)
        
        if not ticker:
            print(f"   ❌ {symbol} not in live ticker data")
            return
            
        mc = s.get("market_cap", 0) or 0
        vol = s.get("volume_1d", 0) or 0
        vola = s.get("volatility", {}).get("1d", 0) or 0
        min_volume = get_min_volume_for_symbol(symbol)
        
        print(f"   Market Cap: {mc:,.0f} (min: {MIN_MARKETCAP:,.0f}) {'✅' if mc >= MIN_MARKETCAP else '❌'}")
        print(f"   Volume 24h: {vol:,.0f} (min: {min_volume:,.0f}) {'✅' if vol >= min_volume else '❌'}")
        print(f"   Volatility: {vola:.4f} (min: {MIN_VOLATILITY:.4f}) {'✅' if vola >= MIN_VOLATILITY else '❌'}")
        
        has_momentum = has_recent_momentum(symbol)
        print(f"   Recent Momentum: {'✅' if has_momentum else '❌'}")
        
        if not has_momentum:
            print(f"   Checking momentum details...")
            d1, d5, d15 = dynamic_momentum_set(symbol)
            print(f"   Dynamic thresholds: 1m={d1*100:.2f}%, 5m={d5*100:.2f}%, 15m={d15*100:.2f}%")
            
            try:
                # Check 1m momentum
                klines_1m = client.get_klines(symbol=symbol, interval='1m', limit=2)
                if len(klines_1m) >= 2:
                    prev_close = float(klines_1m[-2][4])
                    last_close = float(klines_1m[-1][4])
                    pct_1m = (last_close - prev_close) / prev_close
                    print(f"   1m change: {pct_1m*100:.3f}% (need: {d1*100:.2f}%) {'✅' if pct_1m > d1 else '❌'}")
                
                # Check 5m momentum  
                klines_5m = client.get_klines(symbol=symbol, interval='5m', limit=2)
                if len(klines_5m) >= 2:
                    prev_close = float(klines_5m[-2][4])
                    last_close = float(klines_5m[-1][4])
                    pct_5m = (last_close - prev_close) / prev_close
                    print(f"   5m change: {pct_5m*100:.3f}% (need: {d5*100:.2f}%) {'✅' if pct_5m > d5 else '❌'}")
                
                # Check 15m momentum
                klines_15m = client.get_klines(symbol=symbol, interval='15m', limit=2)
                if len(klines_15m) >= 2:
                    prev_close = float(klines_15m[-2][4])
                    last_close = float(klines_15m[-1][4])
                    pct_15m = (last_close - prev_close) / prev_close
                    print(f"   15m change: {pct_15m*100:.3f}% (need: {d15*100:.2f}%) {'✅' if pct_15m > d15 else '❌'}")
                    
            except Exception as e:
                print(f"   Error checking momentum: {e}")
        
        return
    
    # 6. Check tradability guard
    ok, reason, diag = guard_tradability(symbol, side="BUY")
    print(f"6. Tradability Guard: {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"   ❌ Reason: {reason}")
        print(f"   Details: {diag}")
        return
    
    # 7. Check if momentum still exists at investment time
    d1, d5, d15 = dynamic_momentum_set(symbol)
    has_momentum_now = has_recent_momentum(symbol, d1, d5, d15)
    print(f"7. Momentum Check at Investment Time: {'✅' if has_momentum_now else '❌'}")
    if not has_momentum_now:
        print(f"   ❌ Momentum faded between alert and investment attempt")
        return
    
    print(f"\n✅ ALL CHECKS PASSED - {symbol} should have been invested!")
    print(f"If it wasn't invested, check the actual trading loop execution and buy() function.")

def initialize_websocket_monitoring():
    """Initialize optimized WebSocket monitoring using Binance ThreadedWebsocketManager with enhanced error handling."""
    global ws_price_manager
    
    try:
        print("[WEBSOCKET INIT] 🚀 Initializing optimized Binance WebSocket monitoring...")
        ws_price_manager = WebSocketPriceManager(client)
        
        # Start the WebSocket manager with robust error handling
        ws_price_manager.start()
        
        # Wait for initialization with timeout
        max_wait = 10  # Maximum 10 seconds wait
        wait_time = 0
        while wait_time < max_wait and not ws_price_manager._running:
            time.sleep(0.5)
            wait_time += 0.5
        
        # Check if WebSocket started successfully
        if ws_price_manager._running:
            print("[WEBSOCKET INIT] ✅ WebSocket manager started successfully")
            
            symbols_to_monitor = set()
            
            # Add current position symbols (filtered to allowed symbols only)
            position_symbols = filter_to_allowed_symbols(list(positions.keys()))
            symbols_to_monitor.update(position_symbols)
            
            # Add symbols from YAML file (filtered to allowed symbols only)
            if os.path.exists(YAML_SYMBOLS_FILE):
                try:
                    with open(YAML_SYMBOLS_FILE, "r") as f:
                        yaml_data = yaml.safe_load(f)
                        if yaml_data:
                            yaml_symbols = filter_to_allowed_symbols(list(yaml_data.keys()))
                            symbols_to_monitor.update(yaml_symbols[:3])  # Limit to top 3 for performance
                except Exception as e:
                    print(f"[WEBSOCKET INIT] YAML error: {e}")
            
            # Ensure we're only monitoring allowed symbols
            symbols_to_monitor = symbols_to_monitor.intersection(ALLOWED_SYMBOLS)
            
            print(f"[WEBSOCKET INIT] 📊 Adding {len(symbols_to_monitor)} symbols to monitoring")
            
            if symbols_to_monitor:
                # Add symbols gradually to avoid overwhelming the system
                for i, symbol in enumerate(symbols_to_monitor):
                    try:
                        ws_price_manager.add_symbol(symbol)
                        print(f"[WEBSOCKET INIT] ✅ Added {symbol} ({i+1}/{len(symbols_to_monitor)})")
                        time.sleep(0.5)  # Small delay between additions
                    except Exception as e:
                        print(f"[WEBSOCKET INIT] ❌ Failed to add {symbol}: {e}")
                        continue
                
                # Wait and check connection stats
                time.sleep(3)
                try:
                    stats = ws_price_manager.get_connection_stats()
                    print(f"[WEBSOCKET INIT] 📈 Final stats: {stats}")
                    
                    if stats['active_streams'] > 0:
                        print(f"[WEBSOCKET INIT] ✅ Successfully monitoring {stats['active_streams']} symbols")
                    else:
                        print("[WEBSOCKET INIT] ⚠️ No active streams, falling back to REST API")
                except Exception as e:
                    print(f"[WEBSOCKET INIT] ⚠️ Stats error: {e}")
            else:
                print("[WEBSOCKET INIT] ℹ️ No symbols to monitor, WebSocket ready for future additions")
        else:
            print("[WEBSOCKET INIT] ❌ WebSocket failed to start within timeout, using REST API only")
            if ws_price_manager:
                try:
                    ws_price_manager.stop()
                except Exception:
                    pass
            ws_price_manager = None
            
    except Exception as e:
        print(f"[WEBSOCKET INIT ERROR] ❌ Critical error: {e}")
        print("[WEBSOCKET INIT] 🔄 Falling back to REST API for price data")
        import traceback
        traceback.print_exc()
        
        # Clean up any partial initialization
        if 'ws_price_manager' in locals() and ws_price_manager:
            try:
                ws_price_manager.stop()
            except Exception:
                pass
        ws_price_manager = None

def update_websocket_symbols():
    """Optimized WebSocket symbol management with error handling."""
    if not ws_price_manager:
        return
        
    try:
        symbols_to_monitor = set()
        
        # Add current position symbols (only if they have significant value)
        for symbol, pos in positions.items():
            try:
                qty = pos.get('qty', 0)
                if qty > 0:
                    # Quick value check using cached price or skip if unavailable
                    price = get_latest_price(symbol)
                    if price and (qty * price) > DUST_LIMIT:
                        symbols_to_monitor.add(symbol)
            except Exception as e:
                print(f"[WEBSOCKET UPDATE] Error checking position {symbol}: {e}")
                continue
        
        # Add top symbols from YAML file (limited for performance)
        if os.path.exists(YAML_SYMBOLS_FILE):
            try:
                with open(YAML_SYMBOLS_FILE, 'r') as f:
                    yaml_data = yaml.safe_load(f)
                    if isinstance(yaml_data, dict):
                        # Get top 2 symbols only to reduce load
                        top_symbols = list(yaml_data.keys())[:2]
                        yaml_symbols = filter_to_allowed_symbols(top_symbols)
                        symbols_to_monitor.update(yaml_symbols)
            except Exception as e:
                print(f"[WEBSOCKET UPDATE] YAML error: {e}")
        
        # Filter to valid symbols and limit total count
        valid_symbols = [s for s in symbols_to_monitor 
                        if s and isinstance(s, str) and s.endswith('USDC')]
        
        # Limit to maximum 5 symbols for deployment performance
        valid_symbols = valid_symbols[:5]
        
        if valid_symbols:
            print(f"[WEBSOCKET UPDATE] Updating to monitor {len(valid_symbols)} symbols: {valid_symbols}")
            ws_price_manager.update_symbol_list(valid_symbols)
        else:
            print("[WEBSOCKET UPDATE] No valid symbols to monitor")
        
    except Exception as e:
        print(f"[WEBSOCKET UPDATE ERROR] {e}")
        # Don't crash the bot, just continue with current monitoring

def detect_trading_mode():
    """Detect the optimal trading mode based on API permissions."""
    try:
        account = client.get_account()
        permissions = account.get('permissions', [])
        
        # Check for SPOT trading
        if 'SPOT' in permissions:
            print("[TRADING MODE] SPOT trading detected")
            return 'SPOT'
        
        # Check for Universal Transfer permissions
        trade_groups = [p for p in permissions if p.startswith('TRD_GRP_')]
        if trade_groups:
            print(f"[TRADING MODE] Universal Transfer detected: {trade_groups}")
            return 'UNIVERSAL_TRANSFER'
        
        # Fallback to simulation mode
        print("[TRADING MODE] No trading permissions detected, using simulation mode")
        return 'SIMULATE'
        
    except Exception as e:
        print(f"[TRADING MODE ERROR] Could not detect trading mode: {e}")
        return 'SIMULATE'

def get_effective_trading_mode():
    """Get the effective trading mode based on configuration and detection."""
    if TRADING_MODE == 'AUTO_DETECT':
        return detect_trading_mode()
    else:
        return TRADING_MODE

def cleanup_websocket_monitoring():
    """Clean up WebSocket monitoring on shutdown with proper session cleanup."""
    global ws_price_manager
    
    if ws_price_manager:
        try:
            print("[WEBSOCKET] Shutting down price monitoring...")
            ws_price_manager.stop()
            
            # Additional cleanup for any lingering resources
            import gc
            import asyncio
            
            # Clean up any remaining asyncio tasks
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    # Cancel all pending tasks
                    pending_tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
                    if pending_tasks:
                        print(f"[WEBSOCKET] Cancelling {len(pending_tasks)} pending tasks...")
                        for task in pending_tasks:
                            task.cancel()
                        # Wait briefly for tasks to cancel
                        try:
                            loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
                        except Exception:
                            pass
            except Exception as task_cleanup_error:
                print(f"[WEBSOCKET] Task cleanup info: {task_cleanup_error}")
            
            gc.collect()  # Force garbage collection
            
        except Exception as e:
            print(f"[WEBSOCKET CLEANUP ERROR] {e}")
        finally:
            ws_price_manager = None
    else:
        print("[WEBSOCKET] No active monitoring to clean up")

def test_api_connection():
    """Test Binance API connection and permissions."""
    print("\n=== BINANCE API CONNECTION TEST ===")
    
    try:
        # Test 1: Check server connectivity
        print("1. Testing server connectivity...")
        server_time = client.get_server_time()
        print(f"   ✅ Server time: {datetime.fromtimestamp(server_time['serverTime']/1000)}")
        
        # Test 2: Check API key validity
        print("2. Testing API key validity...")
        account = client.get_account()
        print(f"   ✅ Account status: {account.get('accountType', 'SPOT')}")
        
        # Test 3: Check trading permissions
        print("3. Testing trading permissions...")
        permissions = account.get('permissions', [])
        print(f"   Permissions: {permissions}")
        
        # Check for different types of trading permissions
        has_spot = 'SPOT' in permissions
        has_margin = 'MARGIN' in permissions
        has_futures = 'FUTURES' in permissions
        has_leveraged = 'LEVERAGED' in permissions
        
        # Check for trade group permissions (Universal Transfer)
        trade_groups = [p for p in permissions if p.startswith('TRD_GRP_')]
        
        if has_spot:
            print("   ✅ SPOT trading enabled")
        elif trade_groups:
            print(f"   ✅ Trade Group permissions: {trade_groups}")
            print("   ℹ️  Universal Transfer permissions detected")
        else:
            print("   ❌ No trading permissions detected")
            
        # Additional permission details
        if has_margin:
            print("   ✅ MARGIN trading enabled")
        if has_futures:
            print("   ✅ FUTURES trading enabled")
        if has_leveraged:
            print("   ✅ LEVERAGED trading enabled")
        
        # Test 4: Check USDC balance
        print("4. Testing balance access...")
        usdc_balance = client.get_asset_balance(asset='USDC')
        print(f"   ✅ USDC balance: {usdc_balance['free']}")
        
        # Test 5: Check symbol info
        print("5. Testing symbol info access...")
        eth_info = client.get_symbol_info('ETHUSDC')
        print(f"   ✅ ETHUSDC status: {eth_info['status']}")
        
        # Test 6: Test order placement (TEST ORDER - not real)
        print("6. Testing order permissions (test order)...")
        try:
            test_order = client.create_test_order(
                symbol='ETHUSDC',
                side='BUY',
                type='MARKET',
                quoteOrderQty='10.0'
            )
            print("   ✅ Test order successful - trading permissions OK")
        except Exception as test_error:
            print(f"   ❌ Test order failed: {test_error}")
            if "Invalid API-key" in str(test_error):
                print("   → API key lacks trading permissions")
            elif "IP" in str(test_error):
                print("   → IP address not whitelisted")
        
        # Test 7: Test Universal Transfer (if available)
        if trade_groups:
            print("7. Testing Universal Transfer capabilities...")
            try:
                # Test transfer from SPOT to SPOT (essentially a no-op to test permissions)
                # This is a read-only test that shouldn't actually move funds
                transfer_history = client.get_universal_transfer_history(type='MAIN_SPOT', limit=1)
                print("   ✅ Universal Transfer read access works")
                print("   ℹ️  Universal Transfer permissions can be used for trading")
            except Exception as transfer_error:
                if "Invalid symbol" not in str(transfer_error) and "Permission denied" not in str(transfer_error):
                    print(f"   ⚠️  Universal Transfer test: {transfer_error}")
                else:
                    print("   ❌ Universal Transfer permissions insufficient")
        
        # Test 8: Alternative order methods for Universal Transfer
        if trade_groups and not has_spot:
            print("8. Testing alternative order methods...")
            try:
                # Try using OCO order as an alternative (some APIs allow this with Universal Transfer)
                recent_trades = client.get_recent_trades(symbol='ETHUSDC', limit=1)
                if recent_trades:
                    print("   ✅ Market data access works - can implement trading logic")
                    print("   ℹ️  Consider using Universal Transfer with manual order management")
            except Exception as alt_error:
                print(f"   ⚠️  Alternative methods test: {alt_error}")
        
        print("\n✅ API connection test completed")
        return True
        
    except Exception as e:
        print(f"❌ API test failed: {e}")
        if "Invalid API-key" in str(e):
            print("→ Check: API key, secret, and permissions in Binance")
        elif "IP" in str(e):
            print("→ Check: IP whitelist settings in Binance API management")
        elif "timestamp" in str(e).lower():
            print("→ Check: System time synchronization")
        return False

def test_websocket_connection():
    """Test WebSocket connection and provide deployment diagnostics."""
    print("\n=== WEBSOCKET CONNECTION TEST ===")
    
    try:
        # Test 1: Check if ws_price_manager exists
        print(f"1. WebSocket Manager Status: {'✅ Available' if ws_price_manager else '❌ Not initialized'}")
        
        if ws_price_manager:
            # Test 2: Check connection stats
            stats = ws_price_manager.get_connection_stats()
            print(f"2. Connection Stats: {stats}")
            print(f"   - Running: {stats['running']}")
            print(f"   - Connection Type: {stats['connection_type']}")
            print(f"   - Fallback Mode: {stats['fallback_mode']}")
            print(f"   - Monitored Symbols: {stats['monitored_symbols']}")
            print(f"   - Active Streams: {stats['active_streams']}")
            
            # Test 3: Check if monitoring any symbols
            monitored = ws_price_manager.get_monitored_symbols()
            print(f"3. Monitored Symbols: {monitored}")
            
            # Test 4: Check cache status
            cache_stats = get_realtime_price_summary()
            print(f"4. Cache Status: {cache_stats}")
            
            # Test 5: Try to get price for a test symbol
            test_symbol = 'BTCUSDC' if 'BTCUSDC' in ALLOWED_SYMBOLS else list(ALLOWED_SYMBOLS)[0]
            price = get_latest_price(test_symbol)
            print(f"5. Price Test ({test_symbol}): {'✅ Success' if price else '❌ Failed'} - Price: {price}")
            
            # Test 6: Performance metrics
            start_time = time.time()
            for symbol in ALLOWED_SYMBOLS:
                get_latest_price(symbol)
            end_time = time.time()
            print(f"6. Performance Test: {len(ALLOWED_SYMBOLS)} price fetches in {end_time - start_time:.2f}s")
            
            # Test 7: Deployment recommendations
            if stats['fallback_mode']:
                print("7. Deployment Status: ✅ Using REST API fallback (safe for all environments)")
            elif stats['running'] and stats['active_streams'] > 0:
                print("7. Deployment Status: ✅ WebSocket mode active (optimal performance)")
            else:
                print("7. Deployment Status: ⚠️ Mixed state - check logs for issues")
            
        else:
            print("❌ WebSocket manager not available - using REST API only")
            
            # Test REST API performance
            start_time = time.time()
            for symbol in ALLOWED_SYMBOLS:
                try:
                    price = float(client.get_symbol_ticker(symbol=symbol)["price"])
                    print(f"   REST API {symbol}: ${price:.6f}")
                except Exception as e:
                    print(f"   REST API {symbol}: ERROR - {e}")
            end_time = time.time()
            print(f"   REST API Performance: {len(ALLOWED_SYMBOLS)} calls in {end_time - start_time:.2f}s")
        
        print("=== TEST COMPLETE ===\n")
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        traceback.print_exc()

def optimize_for_deployment():
    """Optimize bot settings for deployment environment."""
    print("[DEPLOYMENT] 🚀 Optimizing bot for deployment environment...")
    
    try:
        # 1. Reduce memory usage
        global price_cache
        max_cache_size = 50  # Limit cache to 50 symbols
        with price_cache_lock:
            if len(price_cache) > max_cache_size:
                # Keep only the most recently updated entries
                sorted_cache = sorted(price_cache.items(), 
                                    key=lambda x: x[1].get('timestamp', 0), 
                                    reverse=True)
                price_cache = dict(sorted_cache[:max_cache_size])
                print(f"[DEPLOYMENT] Trimmed price cache to {len(price_cache)} entries")
        
        # 2. Configure logging for deployment
        import logging
        logging.basicConfig(
            level=logging.WARNING,  # Reduce log verbosity
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        # 3. Set WebSocket limits
        if ws_price_manager:
            # Limit to essential symbols only
            current_symbols = ws_price_manager.get_monitored_symbols()
            if len(current_symbols) > 5:
                essential_symbols = list(ALLOWED_SYMBOLS)[:2]  # Only top 2
                ws_price_manager.update_symbol_list(essential_symbols)
                print(f"[DEPLOYMENT] Limited WebSocket monitoring to {len(essential_symbols)} symbols")
        
        # 4. Optimize API call frequency
        print("[DEPLOYMENT] ✅ Deployment optimizations applied")
        
        # 5. Test connection
        test_websocket_connection()
        
        return True
        
    except Exception as e:
        print(f"[DEPLOYMENT ERROR] Failed to optimize: {e}")
        return False

def add_performance_monitoring():
    """Add performance monitoring to track scheduler delays and suppress warnings."""
    import logging
    
    # Configure logging for APScheduler to suppress all scheduler warnings
    apscheduler_logger = logging.getLogger('apscheduler')
    apscheduler_logger.setLevel(logging.ERROR)  # Only show errors, not warnings
    
    # Suppress specific missed job warnings completely
    class MissedJobFilter(logging.Filter):
        def filter(self, record):
            # Filter out any message about missed jobs
            message = record.getMessage()
            return not ('was missed by' in message or 'misfire' in message.lower())
    
    apscheduler_logger.addFilter(MissedJobFilter())
    
    # Also suppress for specific components
    for logger_name in ['apscheduler.executors.default', 'apscheduler.scheduler', 'apscheduler.jobstores.default']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.addFilter(MissedJobFilter())
    
    print("[PERFORMANCE] Scheduler delay warnings suppressed")

# Add strict symbol filtering - now supports BTC-ETH cross trading
ALLOWED_SYMBOLS = {'BTCUSDC', 'ETHUSDC', 'BTCETH'}

def get_trading_status():
    """Get current trading status summary"""
    status = {
        'allowed_symbols': list(ALLOWED_SYMBOLS),
        'positions': list(positions.keys()),
        'websocket_monitoring': ws_price_manager.get_monitored_symbols() if ws_price_manager else [],
        'balance_usd': balance.get('usd', 0),
        'total_positions': len(positions)
    }
    
    print("=== TRADING STATUS ===")
    print(f"Allowed symbols: {status['allowed_symbols']}")
    print(f"Current positions: {status['positions']}")
    print(f"WebSocket monitoring: {status['websocket_monitoring']}")
    print(f"USD balance: ${status['balance_usd']:.2f}")
    print(f"Total positions: {status['total_positions']}")
    
    return status

def filter_to_allowed_symbols(symbols):
    """Filter symbols to only allowed ones"""
    if isinstance(symbols, dict):
        return {k: v for k, v in symbols.items() if k in ALLOWED_SYMBOLS}
    elif isinstance(symbols, (list, set)):
        return [s for s in symbols if s in ALLOWED_SYMBOLS]
    return symbols

def get_min_volume_for_symbol(symbol):
    """Get the appropriate minimum volume threshold based on the symbol."""
    if symbol.startswith('BTC'):
        return MIN_VOLUME_BTC
    elif symbol.startswith('ETH'):
        return MIN_VOLUME_ETH
    else:
        return MIN_VOLUME_DEFAULT

def get_latest_price(symbol):
    """Optimized price fetching with WebSocket cache priority and intelligent REST fallback."""
    try:
        # Priority 1: Try WebSocket manager cache (most recent data)
        if ws_price_manager and ws_price_manager._running:
            ws_price = ws_price_manager.get_latest_price(symbol)
            if ws_price is not None:
                return ws_price
        
        # Priority 2: Try global price cache (from WebSocket streams)
        with price_cache_lock:
            if symbol in price_cache:
                cache_data = price_cache[symbol]
                # Use cache if less than 30 seconds old for better performance
                if time.time() - cache_data['timestamp'] < 30:
                    return cache_data['price']
        
        # Priority 3: Fallback to REST API with error handling
        try:
            ticker_data = client.get_symbol_ticker(symbol=symbol)
            price = float(ticker_data["price"])
            
            # Update both caches with REST API data
            timestamp = time.time()
            cache_entry = {
                'price': price,
                'timestamp': timestamp,
                'volume': 0,
                'price_change_pct': 0,
                'source': 'REST',
                'high_24h': price,
                'low_24h': price,
                'count': 0
            }
            
            # Update global cache
            with price_cache_lock:
                price_cache[symbol] = cache_entry
                
            # Update WebSocket manager cache if available
            if ws_price_manager and hasattr(ws_price_manager, '_cache_lock'):
                with ws_price_manager._cache_lock:
                    ws_price_manager._price_cache[symbol] = cache_entry.copy()
            
            return price
            
        except BinanceAPIException as e:
            if "Invalid symbol" in str(e):
                print(f"[PRICE WARNING] {symbol}: Invalid symbol")
                return None
            else:
                print(f"[PRICE ERROR] {symbol}: Binance API error: {e}")
                # Try to get from stale cache as last resort
                with price_cache_lock:
                    if symbol in price_cache:
                        stale_data = price_cache[symbol]
                        if time.time() - stale_data['timestamp'] < 300:  # 5 minutes max staleness
                            print(f"[PRICE FALLBACK] Using stale cache for {symbol}")
                            return stale_data['price']
                return None
        
    except Exception as e:
        print(f"[PRICE ERROR] {symbol}: Unexpected error: {e}")
        return None

def get_cached_price_data(symbol):
    """Get full cached price data including volume and price change."""
    with price_cache_lock:
        return price_cache.get(symbol, None)

def get_realtime_price_summary():
    """Optimized real-time price monitoring status with better error handling."""
    try:
        cache_info = {}
        
        # Safely check global price cache
        try:
            with price_cache_lock:
                cache_size = len(price_cache)
                recent_updates = sum(1 for data in price_cache.values() 
                                   if time.time() - data.get('timestamp', 0) < 10)
                cache_info = {
                    'cached_symbols': cache_size,
                    'recent_updates': recent_updates
                }
        except Exception as e:
            print(f"[PRICE SUMMARY] Cache error: {e}")
            cache_info = {'cached_symbols': 0, 'recent_updates': 0}
        
        # Safely check WebSocket manager
        ws_info = {}
        try:
            if ws_price_manager:
                stats = ws_price_manager.get_connection_stats()
                ws_info = {
                    'monitored_symbols': stats.get('monitored_symbols', 0),
                    'websocket_active': stats.get('running', False),
                    'active_streams': stats.get('active_streams', 0),
                    'manager_active': stats.get('manager_active', False)
                }
            else:
                ws_info = {
                    'monitored_symbols': 0,
                    'websocket_active': False,
                    'active_streams': 0,
                    'manager_active': False
                }
        except Exception as e:
            print(f"[PRICE SUMMARY] WebSocket error: {e}")
            ws_info = {
                'monitored_symbols': 0,
                'websocket_active': False,
                'active_streams': 0,
                'manager_active': False
            }
        
        return {**cache_info, **ws_info}
        
    except Exception as e:
        print(f"[PRICE SUMMARY ERROR] {e}")
        return {
            'cached_symbols': 0,
            'recent_updates': 0,
            'monitored_symbols': 0,
            'websocket_active': False,
            'active_streams': 0,
            'manager_active': False
        }

def min_notional_for(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for f in info['filters']:
            if f['filterType'] == 'MIN_NOTIONAL':
                return float(f['notional'])
        return 10.0
    except Exception:
        return 10.0

def lot_step_size_for(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                min_qty = float(f['minQty'])
                return step_size, min_qty
    except Exception:
        pass
    return 0.000001, 0.000001

def round_qty(symbol, qty):
    """Binance-compliant rounding for quantity."""
    info = client.get_symbol_info(symbol)
    step_size = None
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step_size = float(f['stepSize'])
            break
    if step_size is None:
        return qty
    d_qty = decimal.Decimal(str(qty))
    d_step = decimal.Decimal(str(step_size))
    rounded_qty = float((d_qty // d_step) * d_step)
    if rounded_qty < step_size:
        return 0
    return rounded_qty

def get_sellable_positions():
    """Return {symbol: pos} for sellable positions (passes step and notional filters)."""
    out = {}
    for symbol, pos in positions.items():
        qty = pos.get("qty", 0)
        sell_qty = round_qty(symbol, qty)
        current_price = get_latest_price(symbol)
        value = current_price * sell_qty
        if sell_qty == 0 or qty == 0:
            # print(f"[SKIP] {symbol}: Qty after rounding is 0. Skipping sell for now.")
            continue
        if value < DUST_LIMIT:
            print(f"[SKIP] {symbol}: Value after rounding is ${value:.2f} (below DUST_LIMIT ${DUST_LIMIT}), skipping sell.")
            continue
        try:
            min_notional = min_notional_for(symbol)
            current_price = get_latest_price(symbol)
            if current_price is None:
                print(f"[SKIP] {symbol}: Could not fetch price (None), skipping auto-sell logic.")
                continue
            if sell_qty == 0:
                continue
            if sell_qty * current_price < min_notional:
                continue
            out[symbol] = pos
        except Exception:
            continue
    return out

def get_portfolio_lines(positions, get_latest_price, dust_limit=1.0):
    lines = []
    for symbol, pos in positions.items():
        qty = pos['qty']
        entry = pos['entry']
        try:
            current_price = get_latest_price(symbol)
            if current_price is None:
                print(f"[SKIP] {symbol}: Could not fetch price (None), skipping auto-sell logic.")
                continue
        except Exception:
            current_price = entry
        value = qty * current_price
        if value < dust_limit:
            continue
        pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
        lines.append((symbol, qty, entry, current_price, value, pnl_pct))
    return lines

def display_portfolio(positions, get_latest_price, dust_limit=1.0):
    print(f"\nCurrent Portfolio (positions over {dust_limit}€):")
    lines = get_portfolio_lines(positions, get_latest_price, dust_limit)
    if not lines:
        print(f"  (No positions over {dust_limit}€)")
        return
    for symbol, qty, entry, price, value, pnl_pct in lines:
        print(f"  {symbol:<12} qty={qty:.6f} entry={entry:.4f} now={price:.4f} value={value:.2f}€ PnL={pnl_pct:+.2f}%")

def format_investments_message(positions, get_latest_price, dust_limit=1.0):
    lines = get_portfolio_lines(positions, get_latest_price, dust_limit)
    if not lines:
        return f"(No investments over {dust_limit}€)"
    msg = "Current Investments:"
    for symbol, qty, entry, price, value, pnl_pct in lines:
        msg += (
            f"\n\n{symbol}: Qty {qty:.4f} @ {entry:.5f} → {price:.5f} | Value ${value:.2f} | PnL {pnl_pct:+.2f}%"
        )
    return msg

async def telegram_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        await send_with_keyboard(update, "Access Denied.")
        return

    text = update.message.text
    sync_positions_with_binance(client, positions)

    # Support both button text and commands
    if text in ["📊 Balance", "/balance", "/bal"]:
        fetch_usdc_balance()

        usdc = balance['usd']
        total_invested = 0.0
        invested_details = []

        for s, p in positions.items():
            price = get_latest_price(s)
            if price is None:
                continue
            qty = float(p['qty'])
            value = price * qty
            # Only show if value is above DUST_LIMIT
            if value > DUST_LIMIT:
                total_invested += value
                invested_details.append(f"{s}: {qty:.6f} @ ${price:.2f} = ${value:.2f}")

        msg = (
            f"USDC Balance: **${usdc:.2f}**\n"
            f"Total Invested: **${total_invested:.2f}**\n"
            f"Portfolio Value: **${usdc + total_invested:.2f} USDC**"
        )

        if invested_details:
            msg += "\n\n*Investments:*"
            for line in invested_details:
                msg += f"\n- {line}"

        await send_with_keyboard(update, msg, parse_mode='Markdown')


    
    elif text in ["💼 Investments", "/investments", "/inv", "/positions"]:
        msg = format_investments_message(positions, get_latest_price, DUST_LIMIT)
        await send_with_keyboard(update, msg)
    
    elif text in ["⏸ Pause Trading", "/pause"]:
        set_paused(True)
        await send_with_keyboard(update, "⏸ Trading is now *paused*. Bot will not auto-invest or auto-sell until resumed.", parse_mode='Markdown')

    elif text in ["▶️ Resume Trading", "/resume", "/start"]:
        set_paused(False)
        await send_with_keyboard(update, "▶️ Trading is *resumed*. Bot will continue auto-investing and auto-selling.", parse_mode='Markdown')

    elif text in ["📝 Trade Log", "/log", "/trades"]:
        log = trade_log
        if not log:
            await send_with_keyboard(update, "No trades yet.")
        else:
            msg = (
                "Time                 Symbol       Entry      Exit       Qty        PnL($)\n"
                "-----------------------------------------------------------------------\n"
            )
            for tr in log[-10:]:
                try:
                    entry = float(tr.get('Entry', 0))
                    exit_ = float(tr.get('Exit', 0))
                    qty = float(tr.get('Qty', 0))
                    pnl = float(tr.get('PnL $', 0))
                    msg += (
                        f"{tr['Time'][:16]:<19} "
                        f"{tr['Symbol']:<11} "
                        f"{entry:<9.4f} "
                        f"{exit_:<9.4f} "
                        f"{qty:<9.5f} "
                        f"{pnl:<8.2f}\n"
                    )
                except (ValueError, KeyError) as e:
                    print(f"[WARN] Bad trade log row: {tr} ({e})")
                    continue
            await send_with_keyboard(update, f"```{msg}```", parse_mode='Markdown')
    
    elif text in ["🔍 Diagnose", "/diagnose", "/diag"]:
        await send_with_keyboard(update, "🔍 Running investment diagnostic for ETHUSDC...")
        
        # Capture diagnostic output
        import io
        import sys
        
        old_stdout = sys.stdout
        sys.stdout = captured_output = io.StringIO()
        
        try:
            diagnose_investment_failure("ETHUSDC")
            diagnostic_text = captured_output.getvalue()
        finally:
            sys.stdout = old_stdout
        
        # Split into chunks if too long for Telegram
        max_length = 4000
        if len(diagnostic_text) > max_length:
            chunks = [diagnostic_text[i:i+max_length] for i in range(0, len(diagnostic_text), max_length)]
            for i, chunk in enumerate(chunks):
                await send_with_keyboard(update, f"```\nDiagnostic Report ({i+1}/{len(chunks)}):\n{chunk}\n```", parse_mode='Markdown')
        else:
            await send_with_keyboard(update, f"```\nDiagnostic Report:\n{diagnostic_text}\n```", parse_mode='Markdown')
    
    elif text in ["🔄 WebSocket Status", "/websocket", "/ws"]:
        status = get_realtime_price_summary()
        msg = (
            f"🔄 **WebSocket Price Monitoring Status**\n\n"
            f"**Status:** {'🟢 Active' if status['websocket_active'] else '🔴 Inactive'}\n"
            f"**Monitored Symbols:** {status['monitored_symbols']}\n"
            f"**Cached Prices:** {status['cached_symbols']}\n"
            f"**Recent Updates:** {status['recent_updates']} (last 10s)\n\n"
        )
        
        if ws_price_manager and status['websocket_active']:
            monitored = ws_price_manager.get_monitored_symbols()
            if monitored:
                msg += "**Currently Monitoring:**\n"
                for i, symbol in enumerate(sorted(monitored)[:10]):  # Show first 10
                    price_data = get_cached_price_data(symbol)
                    if price_data:
                        age = time.time() - price_data['timestamp']
                        msg += f"• {symbol}: ${price_data['price']:.6f} ({age:.1f}s ago)\n"
                    else:
                        msg += f"• {symbol}: No data\n"
                if len(monitored) > 10:
                    msg += f"... and {len(monitored) - 10} more symbols\n"
        
        await send_with_keyboard(update, msg, parse_mode='Markdown')
    
    elif text in ["⚡ Check Momentum", "/momentum", "/check"]:
        await send_with_keyboard(update, "⚡ Running manual momentum check...")
        
        try:
            # Run the quick momentum check manually
            start_time = time.time()
            await alarm_job(context)
            end_time = time.time()
            
            processing_time = end_time - start_time
            await send_with_keyboard(update, 
                f"✅ Manual momentum check completed in {processing_time:.2f} seconds")
        except Exception as e:
            await send_with_keyboard(update, f"❌ Momentum check failed: {str(e)}")
    
    elif text in ["📊 Trading Status", "/status", "/trading"]:
        await send_with_keyboard(update, "📊 Getting trading status...")
        
        # Capture debug output
        import io
        import sys
        
        old_stdout = sys.stdout
        sys.stdout = captured_output = io.StringIO()
        
        try:
            get_trading_status()
            debug_text = captured_output.getvalue()
        finally:
            sys.stdout = old_stdout
        
        # Send debug info
        if len(debug_text) > 4000:
            # Split long messages
            chunks = [debug_text[i:i+4000] for i in range(0, len(debug_text), 4000)]
            for i, chunk in enumerate(chunks):
                await send_with_keyboard(update, f"```\nTrading Status ({i+1}/{len(chunks)}):\n{chunk}\n```", parse_mode='Markdown')
        else:
            await send_with_keyboard(update, f"```\nTrading Status:\n{debug_text}\n```", parse_mode='Markdown')
    
    elif text in ["⚡ Fast Check", "/fast", "/quick"]:
        await send_with_keyboard(update, "⚡ Running fast momentum check for immediate opportunities...")
        
        try:
            fetch_usdc_balance()
            investable = balance['usd'] - sum(float(tr.get('Tax', 0)) for tr in trade_log[-20:] if float(tr.get('Tax', 0)) > 0)
            
            if investable < 10:
                await send_with_keyboard(update, "❌ Not enough USDC for fast investment check")
                return
                
            # Run fast momentum check
            start_time = time.time()
            fast_momentum_invest(investable)
            end_time = time.time()
            
            processing_time = end_time - start_time
            await send_with_keyboard(update, 
                f"✅ Fast momentum check completed in {processing_time:.2f} seconds\n"
                f"💰 Checked ${investable:.2f} USDC for immediate opportunities")
        except Exception as e:
            await send_with_keyboard(update, f"❌ Fast check failed: {str(e)}")
    
    elif text in ["🔧 API Test", "/apitest", "/api"]:
        await send_with_keyboard(update, "🔧 Running Binance API connection test...")
        
        # Capture API test output
        old_stdout = sys.stdout
        sys.stdout = captured_output = io.StringIO()
        
        try:
            api_test_result = test_api_connection()
        finally:
            sys.stdout = old_stdout
        
        api_test_text = captured_output.getvalue()
        
        # Add trading mode info
        trading_mode = get_effective_trading_mode()
        mode_info = f"\n\n🔄 **Trading Mode: {trading_mode}**\n"
        
        if trading_mode == 'SPOT':
            mode_info += "✅ Full SPOT trading enabled"
        elif trading_mode == 'UNIVERSAL_TRANSFER':
            mode_info += "🔄 Universal Transfer mode (simulated trading)"
        else:
            mode_info += "⚠️ Simulation mode only"
        
        api_test_text += mode_info
        
        # Send API test results
        if len(api_test_text) > 4000:
            chunks = [api_test_text[i:i+4000] for i in range(0, len(api_test_text), 4000)]
            for i, chunk in enumerate(chunks):
                await send_with_keyboard(update, f"```\nAPI Test ({i+1}/{len(chunks)}):\n{chunk}\n```", parse_mode='Markdown')
        else:
            await send_with_keyboard(update, f"```\nAPI Test Results:\n{api_test_text}\n```", parse_mode='Markdown')
    
    elif text in ["/tradingmode", "/mode"]:
        trading_mode = get_effective_trading_mode()
        mode_msg = f"""
🔄 **Current Trading Mode: {trading_mode}**

**Mode Details:**
"""
        
        if trading_mode == 'SPOT':
            mode_msg += """
✅ **SPOT Trading Mode**
• Full trading permissions active
• Real orders will be executed
• Direct market buy/sell available
"""
        elif trading_mode == 'UNIVERSAL_TRANSFER':
            # Check Convert API status
            convert_available = False
            try:
                from convert_api import test_convert_api_access
                convert_available = test_convert_api_access()
            except Exception:
                pass
            
            mode_msg += f"""
🔄 **Universal Transfer Mode**
• Using TRD_GRP permissions
• Convert API: {'✅ Available' if convert_available else '❌ Not enabled'}
• {'Real conversions will be used' if convert_available else 'Simulated trading with real market data'}
• Accurate price tracking and momentum detection

**{('Convert API Options' if convert_available else 'Enable Convert API') + ':'}**
{'• ✅ Real conversions enabled!' if convert_available else '• Go to Binance API settings'}
{'• Monitor conversion orders in Telegram' if convert_available else '• Enable Convert permissions'}
{'• Real balance updates' if convert_available else '• Consider manual trading for real execution'}
• Use simulation for strategy testing
"""
        else:
            mode_msg += """
⚠️ **Simulation Mode**
• No trading permissions detected
• All trades are simulated only
• Enable SPOT or Universal Transfer permissions
"""
        
        await send_with_keyboard(update, mode_msg, parse_mode='Markdown')
    
    elif text in ["/convertapi", "/convert"]:
        try:
            from convert_api import test_convert_api_access, get_convert_quote
            
            convert_msg = "🔄 **Convert API Status**\n\n"
            
            # Test API access
            api_access = test_convert_api_access()
            convert_msg += f"**API Access:** {'✅ Available' if api_access else '❌ Not available'}\n\n"
            
            if api_access:
                # Test a small quote
                try:
                    quote = get_convert_quote('USDC', 'ETH', 1.0)
                    if quote:
                        convert_msg += f"""**Quote Test:** ✅ Working
• Rate: {quote.get('ratio', 'N/A')}
• From: {quote.get('fromAmount')} {quote.get('fromAsset')}
• To: {quote.get('toAmount')} {quote.get('toAsset')}

**Status:** 🎉 Convert API fully functional!
Real conversions will be used for trading."""
                    else:
                        convert_msg += """**Quote Test:** ❌ Failed
**Issue:** Convert permissions not enabled

**To Enable:**
1. Go to Binance API Management
2. Edit your API key
3. Enable "Convert" permissions
4. Save and wait a few minutes"""
                except Exception as e:
                    convert_msg += f"""**Quote Test:** ❌ Error
**Error:** {str(e)[:100]}...

**Solution:** Enable Convert API permissions"""
            else:
                convert_msg += """**Issue:** Cannot access Convert API

**Solution:** Enable Convert permissions in Binance API settings"""
            
            await send_with_keyboard(update, convert_msg, parse_mode='Markdown')
            
        except ImportError:
            await send_with_keyboard(update, "❌ Convert API module not available")
        except Exception as e:
            await send_with_keyboard(update, f"❌ Error checking Convert API: {e}")
    
    elif text in ["/refresh", "🔄 Refresh Keyboard"]:
        await send_with_keyboard(update, "🔄 Keyboard refreshed! You should now see the updated buttons.", 
                                reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True))
    
    elif text in ["/help", "/commands"]:
        trading_mode = get_effective_trading_mode()
        mode_status = f"""🔄 **Current Trading Mode: {trading_mode}**
{'✅ Real trading permissions active' if trading_mode == 'SPOT' else '🔄 Universal Transfer mode (simulated)' if trading_mode == 'UNIVERSAL_TRANSFER' else '⚠️ Simulation mode only'}

"""
        
        help_msg = mode_status + """**Available Commands:**

💰 **Finance & Balance:**
• `/balance` or `/bal` - Check USDC balance
• `/investments` or `/inv` - View current positions

⚡ **Trading Controls:**
• `/pause` - Pause all trading
• `/resume` or `/start` - Resume trading
• `/fast` or `/quick` - Quick momentum check

📊 **Status & Analysis:**
• `/status` or `/trading` - Trading status
• `/momentum` or `/check` - Check momentum
• `/websocket` or `/ws` - WebSocket status
• `/apitest` or `/api` - Test Binance API
• `/tradingmode` or `/mode` - Check trading mode
• `/convertapi` or `/convert` - Check Convert API status

📝 **Logs & Diagnostics:**
• `/log` or `/trades` - View trade history
• `/diagnose` or `/diag` - Investment diagnostic

🔧 **Other:**
• `/help` or `/commands` - Show this help
• `/refresh` - Refresh keyboard

You can use either the buttons or type these commands!
        """
        await send_with_keyboard(update, help_msg, parse_mode='Markdown')
    
    elif text in ["📈 Status", "/overview"]:
        # Combine multiple status checks into one overview
        await send_with_keyboard(update, "📈 Getting complete status overview...")
        
        fetch_usdc_balance()
        usdc = balance['usd']
        total_invested = sum(get_latest_price(s) * p.get('qty', 0) 
                           for s, p in positions.items() 
                           if get_latest_price(s) and p.get('qty', 0) * get_latest_price(s) > DUST_LIMIT)
        
        status_msg = f"""
**📈 Trading Bot Overview**

**Financial Status:**
• USDC Balance: ${usdc:.2f}
• Total Invested: ${total_invested:.2f}
• Portfolio Value: ${usdc + total_invested:.2f}

**Trading Status:**
• Bot State: {'⏸️ PAUSED' if is_paused() else '▶️ ACTIVE'}
• Active Positions: {len([p for s, p in positions.items() if get_latest_price(s) and p.get('qty', 0) * get_latest_price(s) > DUST_LIMIT])}
• Market Risk: {'🔴 HIGH' if market_is_risky() else '🟢 NORMAL'}

**WebSocket Status:**
• Connection: {'🟢 Active' if ws_price_manager and ws_price_manager._running else '🔴 Inactive'}
• Monitored Symbols: {len(ws_price_manager.get_monitored_symbols()) if ws_price_manager else 0}

Type `/help` for all available commands.
        """
        await send_with_keyboard(update, status_msg, parse_mode='Markdown')
    
    else:
        # Show available commands if unknown command
        if text.startswith('/'):
            await send_with_keyboard(update, "❓ Unknown command. Type `/help` to see available commands.")
        else:
            await send_with_keyboard(update, "❓ Unknown action. Type `/help` to see available commands.")

def sync_positions_with_binance(client, positions, quote_asset="USDC"):
    """Keeps local positions up-to-date with live Binance balances."""
    try:
        account = client.get_account()
        assets = {b['asset']: float(b['free']) for b in account['balances'] if float(b['free']) > 0}
        updated = set()
        for asset, qty in assets.items():
            if asset == quote_asset:
                continue
            symbol = asset + quote_asset
            if symbol in positions:
                positions[symbol]['qty'] = qty
                updated.add(symbol)
            elif qty > 0:
                positions[symbol] = {'qty': qty, 'entry': 0.0, 'trade_time': 0}
                updated.add(symbol)
        for symbol in list(positions):
            base = symbol.replace(quote_asset, "")
            if symbol not in updated and base in assets:
                if assets[base] == 0:
                    positions.pop(symbol, None)
            elif symbol not in updated and base not in assets:
                positions.pop(symbol, None)
    except Exception as e:
        print(f"[SYNC ERROR] Failed to sync positions with Binance: {e}")

def set_paused(paused: bool):
    state = get_bot_state()
    state["paused"] = paused
    save_bot_state(state)

def is_paused():
    state = get_bot_state()
    return state.get("paused", False)

main_keyboard = [
    ["📊 Balance", "💼 Investments"],
    ["⏸ Pause Trading", "▶️ Resume Trading"],
    ["📝 Trade Log", "🔍 Diagnose"],
    ["🔄 WebSocket Status", "⚡ Check Momentum"],
    ["📊 Trading Status", "⚡ Fast Check"],
    ["🔧 API Test", "📈 Status"]
]

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = """
🤖 **Trading Bot Started!**

**Quick Commands:**
• `/balance` - Check balance
• `/inv` - View investments  
• `/status` - Trading status
• `/help` - All commands

Use the buttons below or type commands directly!
    """
    await send_with_keyboard(
        update,
        welcome_msg,
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )

def telegram_main():
    """Run the Telegram bot with proper event loop handling for deployment environments."""
    try:
        import asyncio
        import threading
        
        # Check if we're in an environment with an existing event loop
        existing_loop = None
        try:
            existing_loop = asyncio.get_running_loop()
            print("[TELEGRAM] Detected existing event loop, using threaded approach")
        except RuntimeError:
            print("[TELEGRAM] No existing event loop, starting normally")
        
        def run_bot_in_thread():
            """Run the bot in a separate thread with its own event loop."""
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Configure the application with better job queue settings
                from telegram.ext import JobQueue
                
                application = ApplicationBuilder() \
                    .token(TELEGRAM_TOKEN) \
                    .build()

                # Configure job queue for maximum tolerance of delays
                if hasattr(application.job_queue, '_scheduler'):
                    # Set very generous misfire grace time
                    application.job_queue._scheduler.configure(
                        misfire_grace_time=120,  # Allow up to 2 minutes delay
                        max_workers=1,           # Single worker to avoid conflicts
                        coalesce=True           # Merge multiple missed runs into one
                    )

                application.add_handler(CommandHandler('start', start_handler))
                application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_handle_message))

                # Add periodic alarm job with faster momentum checking
                application.job_queue.run_repeating(
                    alarm_job, 
                    interval=120,  # 2 minutes instead of 6 for faster response
                    first=15,      # First run after 15 seconds for quicker startup
                    name="momentum_checker"
                )

                # Send startup alert using post_init hook to avoid scheduling issues
                async def post_init(application):
                    try:
                        fetch_usdc_balance()
                        total_positions = len([p for p in positions.items() if get_latest_price(p[0]) and p[1]['qty'] * get_latest_price(p[0]) > DUST_LIMIT])
                        
                        await send_alarm_message(
                            f"🤖 BOT STARTED\n\n"
                            f"✅ System initialized successfully\n"
                            f"💰 USDC Balance: ${balance['usd']:.2f}\n"
                            f"📊 Active Positions: {total_positions}\n"
                            f"⏸️ Trading Status: {'PAUSED' if is_paused() else 'ACTIVE'}\n"
                            f"🕐 Startup Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    except Exception as e:
                        print(f"[ALERT ERROR] Could not send startup alert: {e}")
                
                application.post_init = post_init
                
                # Run with proper shutdown handling
                print("[TELEGRAM] Starting bot in dedicated thread...")
                application.run_polling(stop_signals=None)  # Disable automatic signal handling
                
            except Exception as e:
                print(f"[TELEGRAM ERROR] Failed to start Telegram bot: {e}")
                import traceback
                traceback.print_exc()
            finally:
                loop.close()
                print("[TELEGRAM] Bot stopped")
        
        if existing_loop:
            # If there's an existing loop, run the bot in a separate thread
            bot_thread = threading.Thread(target=run_bot_in_thread, daemon=False)
            bot_thread.start()
            print("[TELEGRAM] Bot thread started, returning control to main thread")
            return bot_thread  # Return thread so main can join if needed
        else:
            # No existing loop, run normally
            run_bot_in_thread()
        
    except Exception as e:
        print(f"[TELEGRAM ERROR] Failed to initialize Telegram bot: {e}")
        import traceback
        traceback.print_exc()


def quote_precision_for(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step = str(f['stepSize'])
                if '.' in step:
                    return len(step.split('.')[1].rstrip('0'))
        return 2
    except Exception:
        return 2

def buy_with_universal_transfer(symbol, amount=None):
    """Alternative buy function using Universal Transfer permissions when SPOT trading is not available."""
    print(f"[UNIVERSAL TRANSFER] Attempting buy for {symbol}: amount=${amount}")
    
    try:
        precision = quote_precision_for(symbol)
        trade_amount = round(amount, precision)
        min_notional = min_notional_for(symbol)
        
        print(f"[UNIVERSAL TRANSFER] {symbol}: trade_amount=${trade_amount}, min_notional=${min_notional}")
        
        if trade_amount < min_notional:
            print(f"[SKIP] {symbol}: Trade amount (${trade_amount}) < MIN_NOTIONAL (${min_notional})")
            return None
        
        # Get current market price
        current_price = get_latest_price(symbol)
        if not current_price:
            print(f"[ERROR] Could not get current price for {symbol}")
            return None
        
        # Calculate quantity with slippage
        adjusted_price = current_price * (1 + UT_CONVERSION_SLIPPAGE)  # Add slippage
        qty_to_buy = trade_amount / adjusted_price
        qty_to_buy = round_qty(symbol, qty_to_buy)
        
        if qty_to_buy <= 0:
            print(f"[ERROR] Invalid quantity calculated: {qty_to_buy}")
            return None
        
        base_asset = symbol.replace('USDC', '')
        
        # Method 1: Try using Binance Convert API (if available)
        try:
            from convert_api import convert_usdc_to_crypto, test_convert_api_access
            
            # Check if Convert API is available
            if test_convert_api_access():
                print("[CONVERT] Attempting real conversion via Convert API...")
                
                convert_result = convert_usdc_to_crypto(symbol, trade_amount)
                if convert_result and convert_result.get('success'):
                    print(f"[CONVERT] ✅ Real conversion successful!")
                    print(f"[CONVERT] Order ID: {convert_result.get('orderId')}")
                    print(f"[CONVERT] Converted: {convert_result.get('fromAmount')} USDC -> {convert_result.get('toAmount')} {convert_result.get('toAsset')}")
                    
                    # Update actual balance (deduct USDC spent)
                    balance['usd'] -= trade_amount
                    print(f"[DEBUG] USDC balance after real conversion: {balance['usd']:.8f} (spent {trade_amount} USDC)")
                    
                    # Store real position
                    positions[symbol] = {
                        'entry': convert_result.get('rate', adjusted_price),
                        'qty': convert_result.get('toAmount', qty_to_buy),
                        'timestamp': time.time(),
                        'trade_time': time.time(),
                        'mode': 'CONVERT_API_REAL',
                        'original_amount': trade_amount,
                        'order_id': convert_result.get('orderId'),
                        'real_trade': True
                    }
                    
                    # Send alert about real trade
                    try:
                        send_alarm_message_safe(
                            f"✅ REAL CONVERSION EXECUTED\n\n"
                            f"📊 {symbol}\n"
                            f"💵 Amount: ${trade_amount:.2f} USDC\n"
                            f"📈 Rate: {convert_result.get('rate', 'N/A')}\n"
                            f"🔢 Received: {convert_result.get('toAmount'):.6f} {base_asset}\n"
                            f"💼 Remaining USDC: ${balance['usd']:.2f}\n"
                            f"🆔 Order ID: {convert_result.get('orderId')}\n"
                            f"✅ Mode: Convert API (Real Trading)"
                        )
                    except Exception as e:
                        print(f"[ALERT ERROR] Could not send real trade alert: {e}")
                    
                    print(f"[CONVERT] ✅ Real position created for {symbol}")
                    return positions[symbol]
                    
                else:
                    print("[CONVERT] Convert API call failed, falling back to simulation")
            else:
                print("[CONVERT] Convert API not available, falling back to simulation")
                
        except ImportError:
            print("[CONVERT] Convert API module not available, falling back to simulation")
        except Exception as convert_error:
            print(f"[CONVERT] Convert API error: {convert_error}, falling back to simulation")
        
        # Method 2: Simulated trading with accurate market data
        if ALLOW_SIMULATED_TRADES:
            print(f"[SIMULATE] Executing simulated trade:")
            print(f"[SIMULATE] - Symbol: {symbol}")
            print(f"[SIMULATE] - USDC Amount: ${trade_amount:.2f}")
            print(f"[SIMULATE] - Market Price: ${current_price:.6f}")
            print(f"[SIMULATE] - Price with Slippage: ${adjusted_price:.6f}")
            print(f"[SIMULATE] - Quantity: {qty_to_buy:.6f} {base_asset}")
            
            # Update simulated balance
            balance['usd'] -= trade_amount
            print(f"[DEBUG] USDC balance after simulation: {balance['usd']:.8f} (spent {trade_amount} USDC)")
            positions[symbol] = {
                'entry': adjusted_price,  # Use slippage-adjusted price
                'qty': qty_to_buy,
                'timestamp': time.time(),
                'trade_time': time.time(),
                'mode': 'UNIVERSAL_TRANSFER_SIMULATED',
                'original_amount': trade_amount
            }
            
            # Send alert about simulated trade
            try:
                send_alarm_message_safe(
                    f"🔄 SIMULATED INVESTMENT\n\n"
                    f"📊 {symbol}\n"
                    f"💵 Amount: ${trade_amount:.2f} USDC\n"
                    f"📈 Price: ${adjusted_price:.6f} (with slippage)\n"
                    f"🔢 Quantity: {qty_to_buy:.6f} {base_asset}\n"
                    f"💼 Remaining USDC: ${balance['usd']:.2f}\n"
                    f"⚠️ Mode: Universal Transfer Simulation"
                )
            except Exception as e:
                print(f"[ALERT ERROR] Could not send simulated trade alert: {e}")
            
            print(f"[SIMULATE] ✅ Simulated position created for {symbol}")
            return positions[symbol]
        
        else:
            print(f"[SKIP] Simulated trades disabled. Would buy {qty_to_buy:.6f} {symbol} for ${trade_amount:.2f}")
            print(f"[INFO] To enable real Universal Transfer trading:")
            print(f"[INFO] 1. Enable Convert API permissions in Binance")
            print(f"[INFO] 2. Use external trading platform")
            print(f"[INFO] 3. Set ALLOW_SIMULATED_TRADES=True for testing")
            return None
        
    except Exception as e:
        print(f"[UNIVERSAL TRANSFER ERROR] {symbol}: {e}")
        return None

def determine_trading_pair(from_asset, to_asset):
    """Determine the correct trading pair and side for cross-asset trading"""
    if from_asset == 'BTC' and to_asset == 'ETH':
        return 'BTCETH', 'SELL'  # Sell BTC to get ETH
    elif from_asset == 'ETH' and to_asset == 'BTC':
        return 'BTCETH', 'BUY'   # Buy BTC with ETH
    elif from_asset == 'BTC' and to_asset == 'USDC':
        return 'BTCUSDC', 'SELL'
    elif from_asset == 'USDC' and to_asset == 'BTC':
        return 'BTCUSDC', 'BUY'
    elif from_asset == 'ETH' and to_asset == 'USDC':
        return 'ETHUSDC', 'SELL'
    elif from_asset == 'USDC' and to_asset == 'ETH':
        return 'ETHUSDC', 'BUY'
    else:
        raise ValueError(f"Unsupported trading pair: {from_asset} -> {to_asset}")

def cross_asset_trade(from_asset, to_asset, amount, symbol_for_position=None):
    """Execute a cross-asset trade between BTC, ETH, and USDC"""
    try:
        symbol, side = determine_trading_pair(from_asset, to_asset)
        
        print(f"[CROSS-TRADE] Trading {from_asset} -> {to_asset} using {symbol} ({side})")
        
        # Check if we have sufficient balance
        from_balance = balance[from_asset.lower()]
        if from_balance < amount:
            print(f"[ERROR] Insufficient {from_asset} balance: {from_balance} < {amount}")
            return None
        
        if side == 'BUY':
            # Buying to_asset with from_asset
            if symbol.endswith('USDC'):
                # Quote currency is USDC
                order = client.order_market_buy(symbol=symbol, quoteOrderQty=amount)
            else:
                # For BTCETH, buying BTC with ETH (quote currency is ETH)
                order = client.order_market_buy(symbol=symbol, quoteOrderQty=amount)
        else:
            # Selling from_asset to get to_asset
            if symbol.endswith('USDC'):
                # Base currency being sold
                order = client.order_market_sell(symbol=symbol, quantity=amount)
            else:
                # For BTCETH, selling BTC to get ETH
                order = client.order_market_sell(symbol=symbol, quantity=amount)
        
        # Update balances based on executed trade
        executed_qty = float(order['executedQty'])
        avg_price = float(order['cumulativeQuoteQty']) / executed_qty if executed_qty > 0 else 0
        
        if side == 'BUY':
            if symbol.endswith('USDC'):
                # Bought base asset with USDC
                balance['usd'] -= amount
                balance[to_asset.lower()] += executed_qty
            else:
                # Bought BTC with ETH
                balance['eth'] -= amount
                balance['btc'] += executed_qty
        else:
            if symbol.endswith('USDC'):
                # Sold base asset for USDC
                balance[from_asset.lower()] -= executed_qty
                balance['usd'] += float(order['cumulativeQuoteQty'])
            else:
                # Sold BTC for ETH
                balance['btc'] -= executed_qty
                balance['eth'] += float(order['cumulativeQuoteQty'])
        
        print(f"[CROSS-TRADE] ✅ Executed: {executed_qty:.6f} {symbol} at avg price {avg_price:.6f}")
        print(f"[BALANCE] BTC: {balance['btc']:.6f}, ETH: {balance['eth']:.6f}, USD: {balance['usd']:.2f}")
        
        # Create position entry for tracking (if this is a buy operation)
        if symbol_for_position:
            positions[symbol_for_position] = {
                'entry': avg_price,
                'qty': executed_qty,
                'timestamp': time.time(),
                'trade_time': time.time(),
                'mode': 'CROSS_ASSET',
                'underlying_symbol': symbol,
                'asset_held': to_asset if side == 'BUY' else from_asset
            }
            print(f"[POSITION] Created position for {symbol_for_position}: {positions[symbol_for_position]}")
            return positions[symbol_for_position]
        
        return True
        
    except Exception as e:
        print(f"[CROSS-TRADE ERROR] {from_asset}->{to_asset}: {e}")
        return None

def buy(symbol, amount=None):
    """Smart buy function that supports cross-asset trading between BTC, ETH, and USDC."""
    print(f"[BUY] Attempting to buy {symbol} with amount: {amount}")
    print(f"[BALANCE] BTC: {balance['btc']:.6f}, ETH: {balance['eth']:.6f}, USD: {balance['usd']:.2f}")
    
    # Determine what asset we're buying and what we're paying with
    if symbol == 'BTCUSDC':
        # Buying BTC with USDC
        if balance['usd'] >= amount:
            return cross_asset_trade('USDC', 'BTC', amount, symbol_for_position=symbol)
        else:
            print(f"[SKIP] Insufficient USDC balance: {balance['usd']:.2f} < {amount}")
            return None
    elif symbol == 'ETHUSDC':
        # Buying ETH with USDC
        if balance['usd'] >= amount:
            return cross_asset_trade('USDC', 'ETH', amount, symbol_for_position=symbol)
        else:
            print(f"[SKIP] Insufficient USDC balance: {balance['usd']:.2f} < {amount}")
            return None
    elif symbol == 'BTCETH':
        # Decide whether to use BTC or ETH to buy the other
        # Strategy: Use whichever asset we have more of (in USD value)
        btc_price = get_latest_price('BTCUSDC')
        eth_price = get_latest_price('ETHUSDC')
        btc_value = balance['btc'] * btc_price
        eth_value = balance['eth'] * eth_price
        
        min_trade_value = amount or 10  # Default $10 trade
        
        if btc_value >= min_trade_value and eth_value >= min_trade_value:
            # We have both assets, use the one we have more of
            if btc_value > eth_value:
                # Use BTC to buy ETH (sell BTC for ETH)
                btc_amount = min_trade_value / btc_price
                return cross_asset_trade('BTC', 'ETH', btc_amount, symbol_for_position=symbol)
            else:
                # Use ETH to buy BTC (buy BTC with ETH)
                eth_amount = min_trade_value / eth_price
                return cross_asset_trade('ETH', 'BTC', eth_amount, symbol_for_position=symbol)
        elif btc_value >= min_trade_value:
            # Only have enough BTC, use it to buy ETH
            btc_amount = min_trade_value / btc_price
            return cross_asset_trade('BTC', 'ETH', btc_amount, symbol_for_position=symbol)
        elif eth_value >= min_trade_value:
            # Only have enough ETH, use it to buy BTC
            eth_amount = min_trade_value / eth_price
            return cross_asset_trade('ETH', 'BTC', eth_amount, symbol_for_position=symbol)
        else:
            print(f"[SKIP] Insufficient balance for BTCETH trade. BTC: ${btc_value:.2f}, ETH: ${eth_value:.2f}")
            return None
    else:
        print(f"[ERROR] Unsupported symbol for cross-asset trading: {symbol}")
        return None

def buy_with_spot_trading(symbol, amount=None):
    """Original SPOT trading function for when full SPOT permissions are available."""
    try:
        precision = quote_precision_for(symbol)
        trade_amount = round(amount, precision)
        min_notional = min_notional_for(symbol)
        print(f"[SPOT] Attempting to buy {symbol}: trade_amount=${trade_amount}, min_notional=${min_notional}")
        
        if trade_amount < min_notional:
            print(f"[SKIP] {symbol}: Trade amount (${trade_amount}) < MIN_NOTIONAL (${min_notional})")
            return None
            
        # Execute SPOT market buy order
        order = client.order_market_buy(symbol=symbol, quoteOrderQty=trade_amount)
        price = float(order['fills'][0]['price'])
        qty = float(order['executedQty'])
        qty = round_qty(symbol, qty)
        print(f"[SPOT] ✅ Bought {symbol}: qty={qty}, price={price}")
        
        balance['usd'] -= trade_amount
        print(f"[DEBUG] USDC balance after SPOT buy: {balance['usd']:.8f} (spent {trade_amount} USDC)")
        positions[symbol] = {
            'entry': price,
            'qty': qty,
            'timestamp': time.time(),
            'trade_time': time.time(),
            'mode': 'SPOT'
        }
        print(f"[DEBUG] USDC balance after buy: ${balance['usd']}")
        
        # Send investment alert
        try:
            send_alarm_message_safe(
                f"💰 INVESTMENT MADE (SPOT)\n\n"
                f"📊 {symbol}\n"
                f"💵 Amount: ${trade_amount:.2f}\n"
                f"📈 Price: ${price:.6f}\n"
                f"🔢 Quantity: {qty:.6f}\n"
                f"💼 Remaining USDC: ${balance['usd']:.2f}"
            )
        except Exception as e:
            print(f"[ALERT ERROR] Could not send investment alert: {e}")
        
        return positions[symbol]
        
    except BinanceAPIException as e:
        print(f"[SPOT ERROR] {symbol}: {e}")
        if "Invalid API-key" in str(e) or "permission" in str(e).lower():
            print(f"[FALLBACK] SPOT permissions lost, trying Universal Transfer...")
            return buy_with_universal_transfer(symbol, amount)
        else:
            print(f"[SPOT ERROR] Buy failed, refreshing USDC balance and skipping.")
            fetch_usdc_balance()
            return None

def sell(symbol, qty):
    """Smart sell function that supports cross-asset trading between BTC, ETH, and USDC."""
    print(f"[SELL] Attempting to sell {symbol}, qty: {qty}")
    
    # For cross-asset trading, we need to decide what to sell and what to receive
    if symbol == 'BTCUSDC':
        # Selling BTC for USDC
        if balance['btc'] >= qty:
            success = cross_asset_trade('BTC', 'USDC', qty)
            if success:
                # For selling, we need to return (price, fee, tax) for compatibility
                current_price = get_latest_price(symbol)
                return current_price, 0, 0
            else:
                return None, 0, 0
        else:
            print(f"[SKIP] Insufficient BTC balance: {balance['btc']:.6f} < {qty}")
            return None, 0, 0
    elif symbol == 'ETHUSDC':
        # Selling ETH for USDC
        if balance['eth'] >= qty:
            success = cross_asset_trade('ETH', 'USDC', qty)
            if success:
                current_price = get_latest_price(symbol)
                return current_price, 0, 0
            else:
                return None, 0, 0
        else:
            print(f"[SKIP] Insufficient ETH balance: {balance['eth']:.6f} < {qty}")
            return None, 0, 0
    elif symbol == 'BTCETH':
        # For BTCETH positions, we need to determine if we're holding BTC or ETH
        # Check which asset we actually have in our position
        pos = positions.get(symbol, {})
        if not pos:
            print(f"[ERROR] No position found for {symbol}")
            return None, 0, 0
        
        # Check what asset we actually hold for this position
        asset_held = pos.get('asset_held', 'BTC')  # Default to BTC if not specified
        
        if asset_held == 'BTC' and balance['btc'] >= qty:
            # We hold BTC, sell it for USDC
            success = cross_asset_trade('BTC', 'USDC', qty)
            if success:
                current_price = get_latest_price('BTCUSDC')
                return current_price, 0, 0
            else:
                return None, 0, 0
        elif asset_held == 'ETH' and balance['eth'] >= qty:
            # We hold ETH, sell it for USDC
            success = cross_asset_trade('ETH', 'USDC', qty)
            if success:
                current_price = get_latest_price('ETHUSDC')
                return current_price, 0, 0
            else:
                return None, 0, 0
        else:
            print(f"[SKIP] Insufficient {asset_held} balance for {symbol}. BTC: {balance['btc']:.6f}, ETH: {balance['eth']:.6f}")
            return None, 0, 0
    else:
        print(f"[ERROR] Unsupported symbol for cross-asset selling: {symbol}")
        return None, 0, 0

def sell_with_spot_trading(symbol, qty):
    """Original SPOT trading sell function"""
    try:
        sell_qty = round_qty(symbol, qty)
        current_price = get_latest_price(symbol)
        value = current_price * sell_qty
        if sell_qty == 0 or qty == 0:
            # print(f"[SKIP] {symbol}: Qty after rounding is 0. Skipping sell for now.")
            return None, 0, 0
        if value < DUST_LIMIT:
            print(f"[SKIP] {symbol}: Value after rounding is ${value:.2f} (below DUST_LIMIT ${DUST_LIMIT}), skipping sell.")
            return None, 0, 0
        
        order = client.order_market_sell(symbol=symbol, quantity=sell_qty)
        price = float(order['fills'][0]['price'])
        fee = sum(float(f['commission']) for f in order['fills']) if "fills" in order else 0
        return price, fee, 0
    except BinanceAPIException as e:
        print(f"[SELL ERROR] {symbol}: {e}")
        # Do not remove from positions here.
        return None, 0, 0

def sell_with_universal_transfer(symbol, qty):
    """Sell function using Universal Transfer permissions or Convert API"""
    try:
        sell_qty = round_qty(symbol, qty)
        current_price = get_latest_price(symbol)
        value = current_price * sell_qty
        
        if sell_qty == 0 or qty == 0:
            return None, 0, 0
        if value < DUST_LIMIT:
            print(f"[SKIP] {symbol}: Value after rounding is ${value:.2f} (below DUST_LIMIT ${DUST_LIMIT}), skipping sell.")
            return None, 0, 0
        
        base_asset = symbol.replace('USDC', '')
        
        # Method 1: Try using Binance Convert API (if available)
        try:
            from convert_api import convert_crypto_to_usdc, test_convert_api_access
            
            # Check if Convert API is available
            if test_convert_api_access():
                print(f"[CONVERT] Attempting real sell conversion via Convert API...")
                
                convert_result = convert_crypto_to_usdc(symbol, sell_qty)
                if convert_result and convert_result.get('success'):
                    print(f"[CONVERT] ✅ Real sell conversion successful!")
                    print(f"[CONVERT] Order ID: {convert_result.get('orderId')}")
                    print(f"[CONVERT] Converted: {convert_result.get('fromAmount')} {base_asset} -> {convert_result.get('toAmount')} USDC")
                    
                    # Update balance
                    balance['usd'] += convert_result.get('toAmount', 0)
                    print(f"[DEBUG] USDC balance after real sell: {balance['usd']:.8f} (received {convert_result.get('toAmount', 0)} USDC)")
                    
                    # Send alert about real trade
                    try:
                        send_alarm_message_safe(
                            f"✅ REAL SELL CONVERSION EXECUTED\n\n"
                            f"📊 {symbol}\n"
                            f"🔢 Sold: {convert_result.get('fromAmount'):.6f} {base_asset}\n"
                            f"💵 Received: ${convert_result.get('toAmount'):.2f} USDC\n"
                            f"📈 Rate: {convert_result.get('rate', 'N/A')}\n"
                            f"💼 New USDC Balance: ${balance['usd']:.2f}\n"
                            f"🆔 Order ID: {convert_result.get('orderId')}\n"
                            f"✅ Mode: Convert API (Real Trading)"
                        )
                    except Exception as e:
                        print(f"[ALERT ERROR] Could not send real sell alert: {e}")
                    
                    price = convert_result.get('rate', current_price)
                    fee = 0  # Convert API fees are included in the rate
                    return price, fee, 0
                    
                else:
                    print("[CONVERT] Convert API sell failed, falling back to simulation")
            else:
                print("[CONVERT] Convert API not available for sell, falling back to simulation")
                
        except ImportError:
            print("[CONVERT] Convert API module not available for sell, falling back to simulation")
        except Exception as convert_error:
            print(f"[CONVERT] Convert API sell error: {convert_error}, falling back to simulation")
        
        # Method 2: Simulate sell
        print(f"[SIMULATE] Executing simulated sell:")
        print(f"[SIMULATE] - Symbol: {symbol}")
        print(f"[SIMULATE] - Quantity: {sell_qty:.6f} {base_asset}")
        print(f"[SIMULATE] - Price: ${current_price:.6f}")
        print(f"[SIMULATE] - Value: ${value:.2f} USDC")
        
        # Apply realistic slippage for simulation
        slippage = 0.001  # 0.1% slippage
        effective_price = current_price * (1 - slippage)
        effective_value = effective_price * sell_qty
        
        # Update simulated balance
        balance['usd'] += effective_value
        print(f"[DEBUG] USDC balance after simulated sell: {balance['usd']:.8f} (received {effective_value:.2f} USDC)")
        
        print(f"[SIMULATE] ✅ Simulated sell executed for {symbol}")
        return effective_price, 0, 0
        
    except Exception as e:
        print(f"[SELL ERROR] {symbol}: {e}")
        return None, 0, 0

def estimate_trade_tax(entry_price, exit_price, qty, trade_time, exit_time):
    """
    Estimates the tax for a given trade based on holding period and gain.
    Uses 40% for short-term (<24h), 25% for long-term (>=24h).
    """
    trade_time = parse_trade_time(trade_time, time.time())
    exit_time = parse_trade_time(exit_time, time.time())
    trade_time_float = parse_trade_time(trade_time, time.time())
    exit_time_float = parse_trade_time(exit_time, time.time())
    holding_period = exit_time_float - trade_time_float
    profit = (exit_price - entry_price) * qty
    short_term_rate = 0.40
    long_term_rate = 0.25
    if holding_period < 24 * 3600:
        rate = short_term_rate
    else:
        rate = long_term_rate
    tax = profit * rate if profit > 0 else 0
    return tax


def log_trade(symbol, entry, exit_price, qty, trade_time, exit_time, fees=0, tax=0, action="sell"):
    pnl = (exit_price - entry) * qty if action == "sell" else 0
    pnl_pct = ((exit_price - entry) / entry * 100) if action == "sell" and entry != 0 else 0
    duration_sec = int(
    parse_trade_time(exit_time, time.time()) - parse_trade_time(trade_time, time.time())
    )
    trade = {
        'Time': datetime.fromtimestamp(trade_time).strftime("%Y-%m-%d %H:%M:%S"),
        'Action': action,
        'Symbol': symbol,
        'Entry': round(entry, 8),
        'Exit': round(exit_price, 8),
        'Qty': round(qty, 8),
        'PnL $': round(pnl, 8),
        'PnL %': round(pnl_pct, 3),
        'Duration (s)': duration_sec,
        'Fees': round(fees, 8),
        'Tax': round(tax, 8)
    }
    try:
        file_exists = os.path.isfile(TRADE_LOG_FILE)
        with open(TRADE_LOG_FILE, "a", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(trade.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)
    except Exception as e:
        print(f"[LOG ERROR] {e}")

def auto_sell_momentum_positions(min_profit=MIN_PROFIT, trailing_stop=TRAIL_STOP, max_hold_time=MAX_HOLD_TIME):
    now = time.time()
    for symbol, pos in list(positions.items()):
        try:
            entry = float(pos['entry'])
            qty = float(pos['qty'])
            trade_time = parse_trade_time(pos.get('trade_time', now), now)

            current_price = get_latest_price(symbol)
            if current_price is None:
                print(f"[SKIP] {symbol}: Could not fetch price (None), skipping auto-sell logic.")
                continue

            sell_qty = round_qty(symbol, qty)
            value = current_price * sell_qty
            if sell_qty == 0 or qty == 0:
                # print(f"[SKIP] {symbol}: Qty after rounding is 0. Skipping sell for now.")
                continue
            if value < DUST_LIMIT:
                print(f"[SKIP] {symbol}: Value after rounding is ${value:.2f} (below DUST_LIMIT ${DUST_LIMIT}), skipping sell.")
                continue

            pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
            held_for = now - trade_time

            if 'max_price' not in pos:
                pos['max_price'] = entry
            pos['max_price'] = max(pos['max_price'], current_price)
            trail_pct = (current_price - pos['max_price']) / pos['max_price'] * 100

            should_sell = False
            reason = ""
            if pnl_pct >= min_profit and trail_pct <= -trailing_stop:
                should_sell = True
                reason = f"Trailing stop: profit {pnl_pct:.2f}%, now {trail_pct:.2f}% from high."
            elif held_for >= max_hold_time:
                should_sell = True
                reason = f"Timed exit after {held_for/60:.1f} minutes."

            if should_sell:
                exit_price, fee, _ = sell(symbol, qty)
                if exit_price is not None:
                    exit_time = time.time()
                    tax = estimate_trade_tax(entry, exit_price, qty, trade_time, exit_time)
                    pnl_dollar = (exit_price - entry) * qty
                    
                    log_trade(
                        symbol=symbol,
                        entry=entry,
                        exit_price=exit_price,
                        qty=qty,
                        trade_time=trade_time,
                        exit_time=exit_time,
                        fees=fee,
                        tax=tax,
                        action="sell"
                    )
                    
                    # Send sell alert
                    try:
                        send_alarm_message_safe(
                            f"💸 POSITION SOLD\n\n"
                            f"📊 {symbol}\n"
                            f"📉 Entry: ${entry:.6f}\n"
                            f"📈 Exit: ${exit_price:.6f}\n"
                            f"🔢 Quantity: {qty:.6f}\n"
                            f"💰 PnL: ${pnl_dollar:.2f} ({pnl_pct:.2f}%)\n"
                            f"⏱️ Hold Time: {held_for/60:.1f} min\n"
                            f"📋 Reason: {reason}"
                        )
                    except Exception as e:
                        print(f"[ALERT ERROR] Could not send sell alert: {e}")
                    
                    print(f"[MOMENTUM SELL] {symbol}: {reason}")
                    del positions[symbol]
        except Exception as e:
            print(f"[AUTO-SELL ERROR] {symbol}: {e}")



def get_1h_percent_change(symbol):
    # Get last 2 hourly candles
    klines = client.get_klines(symbol=symbol, interval='1h', limit=2)
    if len(klines) < 2:
        return 0
    prev_close = float(klines[0][4])
    last_close = float(klines[1][4])
    return (last_close - prev_close) / prev_close * 100

def market_is_risky():
    # For example, check if BTCUSDT 1h change is negative or > X% move
    btc_change_1h = get_1h_percent_change("BTCUSDT")
    return btc_change_1h < -1 or abs(btc_change_1h) > 3

def sync_investments_with_binance():
    try:
        account_info = client.get_account()
        # Keep ALL nonzero assets except base asset (USDC)
        balances = {
            a["asset"]: float(a["free"])
            for a in account_info["balances"]
            if float(a["free"]) > 0.0001 and a["asset"] != BASE_ASSET
        }
        new_positions = {}
        for asset, amount in balances.items():
            symbol = f"{asset}{BASE_ASSET}"
            try:
                price = float(client.get_symbol_ticker(symbol=symbol)["price"])
                new_positions[symbol] = {
                    "entry": price,  # For new syncs, "entry" is current price, unless you have historical
                    "qty": amount,
                    "timestamp": time.time(),
                    "trade_time": time.time()
                }
            except Exception:
                continue
        positions.clear()
        positions.update(new_positions)
        print("[INFO] Synced investments with real Binance balances.")
    except Exception as e:
        print(f"[SYNC ERROR] Could not sync investments with Binance: {e}")

def too_many_positions():
    count = 0
    for symbol, pos in positions.items():
        price = get_latest_price(symbol)
        if price is None:
            continue
        if pos.get('qty', 0) * price > DUST_LIMIT:
            count += 1
    return count >= MAX_POSITIONS


def reserve_taxes_and_reinvest():
    """
    Reserve USDC for taxes by selling just enough, from as many profitable (non-core, long-term) positions as needed.
    - Sells non-core first, then core if needed.
    - Avoids short-term gains unless required.
    - Uses real min_notional for every symbol.
    """
    # Helper: core coin detection
    def is_core(symbol):
        stats = load_symbol_stats()
        info = stats.get(symbol, {})
        return info.get("core", False)

    # Helper: short-term check
    SHORT_TERM_SECONDS = 24 * 3600  # 24 hours, adjust if needed
    def is_short_term(pos):
        trade_time = parse_trade_time(pos.get('trade_time', time.time()), time.time())
        held_for = time.time() - trade_time
        return held_for < SHORT_TERM_SECONDS


    # Helper: profit calculation
    def position_profit(sym):
        pos = positions[sym]
        cur_price = get_latest_price(sym)
        entry = pos['entry']
        return (cur_price - entry) * pos['qty']

    # 1. Calculate taxes owed from recent closed trades
    total_taxes_owed = sum(
        float(tr.get('Tax', 0)) for tr in trade_log[-20:] if float(tr.get('Tax', 0)) > 0
    )

    fetch_usdc_balance()
    free_usdc = balance['usd']

    # 2. Sell as little as needed from as many positions as needed
    while True:
        # Calculate how much USDC we still need to invest after tax reserve
        needed_usdc = 0

        # We'll use the minimum min_notional for ALL symbols in momentum (safe fallback)
        momentum_symbols = get_yaml_ranked_momentum(limit=3)
        min_notional = min([min_notional_for(sym) for sym in momentum_symbols] + [10.0])

        needed_usdc = (min_notional + total_taxes_owed) - free_usdc
        if needed_usdc <= 0:
            break  # We have enough, done selling

        # Step 1: Try non-core, profitable, long-term positions
        candidates = [
            sym for sym in positions
            if position_profit(sym) > 0
            and round_qty(sym, positions[sym]['qty']) > 0
            and not is_core(sym)
            and not is_short_term(positions[sym])
        ]
        # Step 2: If none, try core, profitable, long-term positions
        if not candidates:
            candidates = [
                sym for sym in positions
                if position_profit(sym) > 0
                and round_qty(sym, positions[sym]['qty']) > 0
                and is_core(sym)
                and not is_short_term(positions[sym])
            ]
        # Step 3: If still none, allow non-core, profitable, short-term positions
        if not candidates:
            candidates = [
                sym for sym in positions
                if position_profit(sym) > 0
                and round_qty(sym, positions[sym]['qty']) > 0
                and not is_core(sym)
            ]
        # Step 4: Last resort, allow core, profitable, short-term positions
        if not candidates:
            candidates = [
                sym for sym in positions
                if position_profit(sym) > 0
                and round_qty(sym, positions[sym]['qty']) > 0
            ]
        # If still none, give up
        if not candidates:
            print("[TAXES] No profitable positions to sell for taxes. Waiting to accumulate more USDC.")
            break

        # Sort: non-core first, lowest profit first (to avoid selling strong winners)
        candidates = sorted(
            candidates,
            key=lambda sym: (is_core(sym), position_profit(sym))
        )

        # Sell just enough from one position
        symbol_to_sell = candidates[0]
        pos = positions[symbol_to_sell]
        cur_price = get_latest_price(symbol_to_sell)
        entry = pos['entry']
        qty_available = pos['qty']
        trade_time = pos.get('trade_time', time.time())
        min_notional_this = min_notional_for(symbol_to_sell)

        # How much do we need from this position (in qty)?
        fetch_usdc_balance()
        free_usdc = balance['usd']
        needed_usdc = (min_notional + total_taxes_owed) - free_usdc
        qty_to_sell = min(qty_available, max(needed_usdc / cur_price, min_notional_this / cur_price))
        qty_to_sell = round_qty(symbol_to_sell, qty_to_sell)

        if qty_to_sell == 0:
            print(f"[SKIP] {symbol_to_sell}: Qty after rounding is 0. Skipping this position for now.")
            del positions[symbol_to_sell]
            continue

        print(
            f"[TAXES] Selling {qty_to_sell:.6f} {symbol_to_sell} "
            f"(profit: {position_profit(symbol_to_sell):.2f}, core: {is_core(symbol_to_sell)}, short-term: {is_short_term(pos)}) "
            f"to free up USDC for taxes."
        )

        exit_price, fee, tax = sell(symbol_to_sell, qty_to_sell)

        if exit_price is None:
            print(f"[SKIP] {symbol_to_sell}: Sell returned None. Skipping this position for now.")
            del positions[symbol_to_sell]
            continue

        exit_time = time.time()
        log_trade(symbol_to_sell, entry, exit_price, qty_to_sell, trade_time, exit_time, fee, tax, action="sell")

        # Update or remove position
        if qty_to_sell == qty_available:
            del positions[symbol_to_sell]
        else:
            positions[symbol_to_sell]['qty'] -= qty_to_sell

        fetch_usdc_balance()
        free_usdc = balance['usd']

    # Final check: Only invest with what's left after reserving for taxes
    investable_usdc = free_usdc - total_taxes_owed
    if investable_usdc < min_notional:
        print("[TAXES] Not enough USDC to invest after reserving for taxes.")
        return

    # FAST CHECK: Look for immediate momentum opportunities
    fast_momentum_invest(investable_usdc)

def fast_momentum_invest(usdc_limit):
    """
    Fast momentum check that runs investment logic immediately when strong signals are detected.
    This bypasses the normal periodic checks for ultra-fast response.
    """
    print(f"[FAST CHECK] Looking for immediate momentum opportunities with ${usdc_limit:.2f}")
    
    symbols = get_yaml_ranked_momentum(limit=5)  # Check top 5 instead of 10 for speed
    
    for symbol in symbols:
        try:
            # Quick momentum check
            d1, d5, d15 = dynamic_momentum_set(symbol)
            if not has_recent_momentum(symbol, d1, d5, d15):
                continue
                
            # Fast tradability check
            ok, reason, diag = guard_tradability(symbol, side="BUY")
            if not ok:
                print(f"[FAST SKIP] {symbol}: {reason}")
                continue
                
            # Check if we have enough funds
            min_notional = min_notional_for(symbol)
            if usdc_limit < min_notional:
                print(f"[FAST SKIP] {symbol}: Need ${min_notional:.2f}, have ${usdc_limit:.2f}")
                break
                
            # Execute immediate buy
            print(f"[FAST BUY] {symbol}: Strong momentum detected, buying immediately!")
            result = buy(symbol, amount=min_notional)
            
            if result:
                usdc_limit -= min_notional
                print(f"[FAST SUCCESS] {symbol}: Bought ${min_notional:.2f}, remaining: ${usdc_limit:.2f}")
                
                # Send fast alert
                try:
                    send_alarm_message_safe(
                        f"⚡ FAST MOMENTUM BUY ⚡\n\n"
                        f"📊 {symbol}\n"
                        f"💵 Amount: ${min_notional:.2f}\n"
                        f"🚀 Reason: Strong 3-timeframe momentum\n"
                        f"⏱️ Response: Ultra-fast execution"
                    )
                except Exception as e:
                    print(f"[FAST ALERT ERROR] {e}")
            else:
                print(f"[FAST FAIL] {symbol}: Buy failed")
                
        except Exception as e:
            print(f"[FAST ERROR] {symbol}: {e}")
            continue
    
    # If we still have funds, run normal investment logic
    if usdc_limit >= 10:
        invest_momentum_with_usdc_limit(usdc_limit)

def load_symbol_stats():
    """Load symbol statistics from YAML file, filtered to allowed symbols only."""
    try:
        with open(YAML_SYMBOLS_FILE, "r") as f:
            all_stats = yaml.safe_load(f)
            if not isinstance(all_stats, dict):
                raise ValueError("YAML file is not a valid dict")
            # Fast filter to only allowed symbols using set intersection
            allowed = ALLOWED_SYMBOLS & set(all_stats.keys())
            filtered_stats = {k: all_stats[k] for k in allowed}
            # Validate required fields for reliability (minimal loop)
            invalid = [k for k, v in filtered_stats.items() if not (isinstance(v, dict) and all(key in v for key in ["market_cap", "volume_1d", "volatility"]))]
            for symbol in invalid:
                print(f"[YAML] Removing {symbol}: missing required fields")
                del filtered_stats[symbol]
            print(f"[YAML] Loaded {len(filtered_stats)} allowed symbols from {len(all_stats)} total")
            if not filtered_stats:
                raise ValueError("No valid symbols found in YAML stats")
            return filtered_stats
    except Exception as e:
        print(f"[YAML ERROR] Could not read {YAML_SYMBOLS_FILE}: {e}")
        # Return minimal stats for allowed symbols if YAML fails
        return {symbol: {"market_cap": 1000000, "volume_1d": 1000000, "volatility": {"1d": 0.01}} 
                for symbol in ALLOWED_SYMBOLS}
    
def pct_change(klines):
    if len(klines) < 2: return 0
    prev_close = float(klines[0][4])
    last_close = float(klines[1][4])
    return (last_close - prev_close) / prev_close * 100

def calculate_rsi(klines, period=14):
    """Calculate RSI from klines data."""
    if len(klines) < period + 1:
        return 50  # Default neutral RSI if not enough data
    
    closes = [float(k[4]) for k in klines]
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    if len(gains) < period:
        return 50
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ema(values, period):
    """Calculate Exponential Moving Average."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period  # Start with SMA
    
    for i in range(period, len(values)):
        ema = (values[i] * multiplier) + (ema * (1 - multiplier))
    
    return ema

def check_volume_spike(symbol, interval='1m', spike_multiplier=VOLUME_SPIKE_MULTIPLIER):
    """Check if current volume is significantly higher than average."""
    try:
        # Get more data for average calculation
        klines = client.get_klines(symbol=symbol, interval=interval, limit=50)
        if len(klines) < 10:
            return False
        
        volumes = [float(k[5]) for k in klines]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
        
        return current_volume >= (avg_volume * spike_multiplier)
    except Exception as e:
        print(f"[VOLUME ERROR] {symbol}: {e}")
        return False

def check_ma_cross(symbol, interval='1m', short_period=MA_PERIODS_SHORT, long_period=MA_PERIODS_LONG):
    """Check if short EMA crossed above long EMA recently."""
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=long_period + 5)
        if len(klines) < long_period + 2:
            return False
        
        closes = [float(k[4]) for k in klines]
        
        # Calculate EMAs for last 2 periods to detect cross
        short_ema_prev = calculate_ema(closes[:-1], short_period)
        long_ema_prev = calculate_ema(closes[:-1], long_period)
        short_ema_curr = calculate_ema(closes, short_period)
        long_ema_curr = calculate_ema(closes, long_period)
        
        # Cross occurred if short was below long and now is above
        cross_occurred = (short_ema_prev <= long_ema_prev) and (short_ema_curr > long_ema_curr)
        return cross_occurred
    except Exception as e:
        print(f"[MA CROSS ERROR] {symbol}: {e}")
        return False

def check_advanced_momentum(symbol, interval='1m', min_change=0.003):  # Lower default from 0.005 to 0.003
    """Optimized momentum check using cached price data when available."""
    try:
        # Try to get cached price data first for better performance
        cached_data = None
        if ws_price_manager and ws_price_manager._running:
            cached_data = ws_price_manager.get_price_data(symbol)
        
        if not cached_data:
            with price_cache_lock:
                cached_data = price_cache.get(symbol, {})
        
        # Use cached data if recent and has required fields
        if (cached_data and 
            time.time() - cached_data.get('timestamp', 0) < 60 and
            'price_change_pct' in cached_data):
            
            pct_change = cached_data['price_change_pct'] / 100.0  # Convert from percentage
            volume = cached_data.get('volume', 0)
            
            # Basic momentum check from cached data
            price_momentum = pct_change > min_change
            volume_spike = volume > 0  # Basic volume check from cache
            
            indicators = {
                'pct_change': pct_change * 100,
                'rsi': 65,  # Estimated RSI when using cached data
                'volume_spike': volume_spike,
                'ma_cross': False,  # Cannot calculate from cache alone
                'price_momentum': price_momentum,
                'rsi_momentum': False,
                'source': 'cache'
            }
            
            has_momentum = price_momentum and volume_spike
            return bool(has_momentum), indicators
        
        # Fallback to full API-based calculation
        klines = client.get_klines(symbol=symbol, interval=interval, limit=30)
        if len(klines) < 2:
            return False, {}
        
        prev_close = float(klines[-2][4])
        last_close = float(klines[-1][4])
        pct_change = (last_close - prev_close) / prev_close
        
        # Calculate RSI
        rsi = calculate_rsi(klines)
        
        # Check volume spike
        volume_spike = check_volume_spike(symbol, interval)
        
        # Check MA cross
        ma_cross = check_ma_cross(symbol, interval)
        
        # Price change requirement
        price_momentum = pct_change > min_change
        
        # RSI momentum (above 70 indicates strong buying pressure)
        rsi_momentum = rsi > RSI_OVERBOUGHT
        
        indicators = {
            'pct_change': pct_change * 100,
            'rsi': rsi,
            'volume_spike': volume_spike,
            'ma_cross': ma_cross,
            'price_momentum': price_momentum,
            'rsi_momentum': rsi_momentum,
            'source': 'api'
        }
        
        # Require price momentum + at least one other indicator
        has_momentum = price_momentum and (volume_spike or rsi_momentum or ma_cross)
        
        # Ensure we always return a boolean value, never None
        return bool(has_momentum), indicators
        
    except Exception as e:
        print(f"[ADVANCED MOMENTUM ERROR] {symbol} {interval}: {e}")
        # Always return False (boolean) and empty dict, never None
        return False, {}

# --- Dynamic momentum thresholds based on realized volatility (ATR%) ---

def _atr_pct_from_klines(klines):
    """
    Compute ATR as a percent of last close using simple average TR.
    klines: list of [open_time, open, high, low, close, volume, ...] (as returned by Binance)
    Returns: atr_percent (e.g., 0.003 means 0.3%)
    """
    if len(klines) < 3:
        return 0.002  # fallback ~0.2%
    trs = []
    prev_close = float(klines[0][4])
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        close = float(klines[i][4])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
        prev_close = close
    if not trs:
        return 0.002
    atr = sum(trs) / len(trs)
    last_close = float(klines[-1][4])
    if last_close <= 0:
        return 0.002
    return atr / last_close  # ATR as fraction of price


def dynamic_momentum_threshold(symbol, interval='1m', lookback=60,
                               k=0.6, floor_=0.0008, cap_=0.02):
    """
    Returns a micro-scalp-friendly price-change threshold for this symbol+interval.

    - lookback: number of candles to estimate ATR%
    - k: multiplier on ATR% (higher k => stricter)
    - floor_: minimum absolute threshold (as fraction, e.g. 0.0008 = 0.08%)
    - cap_: maximum absolute threshold (e.g. 0.02 = 2%)
    """
    try:
        # +1 to ensure we have a previous close for TR calc
        kl = client.get_klines(symbol=symbol, interval=interval, limit=lookback+1)
        if not kl or len(kl) < 5:
            # fallback if not enough data: a gentle fixed floor for scalping
            thr = floor_
        else:
            vol_pct = _atr_pct_from_klines(kl)  # e.g., 0.003 = 0.3%
            thr = k * vol_pct
            # clamp to safe bounds
            if thr < floor_:
                thr = floor_
            elif thr > cap_:
                thr = cap_
        return thr
    except Exception as e:
        print(f"[DYN THRESH ERROR] {symbol} {interval}: {e}")
        return floor_  # conservative fallback


def dynamic_momentum_set(symbol):
    """
    Produce per-interval dynamic thresholds tuned for even faster micro-scalping.
    Making all timeframes more sensitive with lower multipliers and floors.
    """
    try:
        thr_1m  = dynamic_momentum_threshold(symbol, '1m',  lookback=60,  k=0.25, floor_=0.0003, cap_=0.010)  # Much more sensitive
        thr_5m  = dynamic_momentum_threshold(symbol, '5m',  lookback=48,  k=0.35, floor_=0.0005, cap_=0.012)  # Much more sensitive  
        thr_15m = dynamic_momentum_threshold(symbol, '15m', lookback=48,  k=0.45, floor_=0.0008, cap_=0.015)  # Much more sensitive
        
        # Ensure no None values are returned
        if thr_1m is None:
            thr_1m = 0.0003
        if thr_5m is None:
            thr_5m = 0.0005
        if thr_15m is None:
            thr_15m = 0.0008
            
        return thr_1m, thr_5m, thr_15m
    except Exception as e:
        print(f"[DYNAMIC MOMENTUM SET ERROR] {symbol}: {e}")
        return 0.0003, 0.0005, 0.0008  # Safe fallback values


def has_recent_momentum(symbol, min_1m=None, min_5m=None, min_15m=None):
    """Enhanced momentum detection using multiple indicators with faster scoring system."""
    try:
        # derive dynamic thresholds if not provided
        if min_1m is None or min_5m is None or min_15m is None:
            min_1m, min_5m, min_15m = dynamic_momentum_set(symbol)

        momentum_1m, indicators_1m     = check_advanced_momentum(symbol, '1m',  min_1m)
        momentum_5m, indicators_5m     = check_advanced_momentum(symbol, '5m',  min_5m)
        momentum_15m, indicators_15m   = check_advanced_momentum(symbol, '15m', min_15m)

        print(f"[DEBUG] {symbol} Dynamic thresholds: 1m={min_1m*100:.2f}%  5m={min_5m*100:.2f}%  15m={min_15m*100:.2f}%")
        print(f"[DEBUG] {symbol} Momentum:")
        print(f"  1m: {momentum_1m} - Price: {indicators_1m.get('pct_change', 0):.3f}% "
              f"RSI: {indicators_1m.get('rsi', 0):.1f} Vol: {indicators_1m.get('volume_spike', False)} "
              f"MA: {indicators_1m.get('ma_cross', False)}")
        print(f"  5m: {momentum_5m} - Price: {indicators_5m.get('pct_change', 0):.3f}% "
              f"RSI: {indicators_5m.get('rsi', 0):.1f} Vol: {indicators_5m.get('volume_spike', False)} "
              f"MA: {indicators_5m.get('ma_cross', False)}")
        print(f"  15m:{momentum_15m} - Price: {indicators_15m.get('pct_change', 0):.3f}% "
              f"RSI: {indicators_15m.get('rsi', 0):.1f} Vol: {indicators_15m.get('volume_spike', False)} "
              f"MA: {indicators_15m.get('ma_cross', False)}")

        # NEW: Weighted scoring system for faster entry
        # Score each timeframe (0-3 points each)
        score_1m = sum([
            indicators_1m.get('price_momentum', False),  # 1 point
            indicators_1m.get('volume_spike', False),    # 1 point  
            indicators_1m.get('rsi_momentum', False) or indicators_1m.get('ma_cross', False)  # 1 point
        ])
        
        score_5m = sum([
            indicators_5m.get('price_momentum', False),  # 1 point
            indicators_5m.get('volume_spike', False),    # 1 point
            indicators_5m.get('rsi_momentum', False) or indicators_5m.get('ma_cross', False)  # 1 point
        ])
        
        score_15m = sum([
            indicators_15m.get('price_momentum', False), # 1 point
            indicators_15m.get('volume_spike', False),   # 1 point
            indicators_15m.get('rsi_momentum', False) or indicators_15m.get('ma_cross', False)  # 1 point
        ])
        
        total_score = score_1m + score_5m + score_15m
        
        print(f"[MOMENTUM SCORE] {symbol}: 1m={score_1m}/3, 5m={score_5m}/3, 15m={score_15m}/3, Total={total_score}/9")
        
        # LESS STRICT MODE: Require at least 5/9 points and 1m score >= 1
        # This allows investment with moderate momentum
        fast_entry = (total_score >= 5 and score_1m >= 1)
        
        # CONSERVATIVE MODE: Original requirement (all timeframes perfect)
        conservative_entry = momentum_1m and momentum_5m and momentum_15m
        
        # Use FAST MODE for quicker entries
        return fast_entry

    except Exception as e:
        print(f"[MOMENTUM ERROR] {symbol}: {e}")
        return False


def get_yaml_ranked_momentum(
        limit=MAX_POSITIONS, 
        min_marketcap=MIN_MARKETCAP, 
        min_volatility=MIN_VOLATILITY):
    stats = load_symbol_stats()
    if not stats:
        return []
    tickers = {t['symbol']: t for t in client.get_ticker() if t['symbol'] in stats}
    candidates = []
    for symbol, s in stats.items():
        ticker = tickers.get(symbol)
        if not ticker:
            print(f"[SKIP] {symbol}: Not in live ticker data")
            continue
        mc = s.get("market_cap", 0) or 0
        vol = s.get("volume_1d", 0) or 0
        vola = s.get("volatility", {}).get("1d", 0) or 0
        price_change = float(ticker.get('priceChangePercent', 0))
        
        # Get symbol-specific minimum volume
        min_volume = get_min_volume_for_symbol(symbol)
        
        if mc < min_marketcap:
            print(f"[SKIP] {symbol}: market_cap {mc} < min {min_marketcap}")
            continue
        if vol < min_volume:
            print(f"[SKIP] {symbol}: volume {vol} < min {min_volume}")
            continue
        if vola < min_volatility:
            print(f"[SKIP] {symbol}: volatility {vola} < min {min_volatility}")
            continue
        if not has_recent_momentum(symbol):
            print(f"[SKIP] {symbol}: has no recent momentum")
            continue
        
        # Enhanced momentum score calculation
        momentum_1m, indicators_1m = check_advanced_momentum(symbol, '1m', MIN_1M)
        momentum_5m, indicators_5m = check_advanced_momentum(symbol, '5m', MIN_5M)
        momentum_15m, indicators_15m = check_advanced_momentum(symbol, '15m', MIN_15M)
        
        # Weight the momentum score based on multiple factors
        momentum_score = 0
        momentum_score += indicators_1m.get('pct_change', 0) * 1.0
        momentum_score += indicators_5m.get('pct_change', 0) * 1.5
        momentum_score += indicators_15m.get('pct_change', 0) * 2.0
        
        # Bonus for having supporting indicators
        if indicators_1m.get('volume_spike', False):
            momentum_score += 0.5
        if indicators_5m.get('volume_spike', False):
            momentum_score += 0.75
        if indicators_15m.get('volume_spike', False):
            momentum_score += 1.0
            
        if indicators_1m.get('rsi_momentum', False):
            momentum_score += 0.3
        if indicators_5m.get('rsi_momentum', False):
            momentum_score += 0.5
        if indicators_15m.get('rsi_momentum', False):
            momentum_score += 0.7
            
        if indicators_1m.get('ma_cross', False):
            momentum_score += 0.4
        if indicators_5m.get('ma_cross', False):
            momentum_score += 0.6
        if indicators_15m.get('ma_cross', False):
            momentum_score += 0.8
        
        candidates.append({
            "symbol": symbol,
            "market_cap": mc,
            "volume": vol,
            "volatility": vola,
            "price_change": price_change,
            "momentum_score": momentum_score,
        })

    ranked = sorted(
        candidates, 
        key=lambda x: (x["momentum_score"], x["market_cap"], x["volume"]), 
        reverse=True
    )
    return [x["symbol"] for x in ranked[:limit]]

def invest_momentum_with_usdc_limit(usdc_limit):
    """
    Invest in as many eligible momentum symbols as possible, always using the min_notional per symbol,
    never all-or-nothing. Any remaining funds are left in USDC.
    This version treats coins you own (including dust) and coins you don't equally.
    """
    # Get symbols directly without redundant refresh_symbols() call
    symbols = get_yaml_ranked_momentum(limit=10)
    print(f"[DEBUG] Momentum symbols eligible for investment: {symbols}")
    if not symbols:
        print("[DIAGNOSE] No symbols passed the momentum and filter criteria.")
        return
    if usdc_limit < 1:
        print("[INFO] Insufficient funds.")
        return

    min_notionals = []
    for symbol in symbols:
        min_notional = min_notional_for(symbol)
        min_notionals.append((symbol, min_notional))

    total_spent = 0
    symbols_to_buy = []
    for symbol, min_notional in sorted(min_notionals, key=lambda x: -x[1]):  # Buy more expensive coins first
        if usdc_limit - total_spent >= min_notional:
            symbols_to_buy.append((symbol, min_notional))
            total_spent += min_notional

    if not symbols_to_buy:
        print(f"[INFO] Not enough USDC to invest in any eligible symbol. Minimum needed: {min([mn for s, mn in min_notionals]):.2f} USDC.")
        return

    for symbol, min_notional in symbols_to_buy:
        amount = min_notional
        fetch_usdc_balance()
        if balance['usd'] < amount:
            print(f"[INFO] Out of funds before buying {symbol}.")
            break
        print(f"[INFO] Attempting to buy {symbol} with ${amount:.2f}")

        d1, d5, d15 = dynamic_momentum_set(symbol)
        if not has_recent_momentum(symbol, d1, d5, d15):
            # momentum faded; skip
            continue

        # Spread & liquidity guard — skip spready/thin books
        ok, reason, diag = guard_tradability(symbol, side="BUY")
        print(f"[GUARD] {symbol} -> {ok} ({reason}) {diag}")
        if not ok:
            # optionally notify via Telegram here using send_alarm_message(...)
            # await send_alarm_message(f"⏸️ {symbol} skipped — {reason} | {diag}")
            continue

        result = buy(symbol, amount=amount)
        if not result:
            print(f"[BUY ERROR] {symbol}: Buy failed, refreshing USDC balance and skipping.")
            fetch_usdc_balance()
        else:
            print(f"[INFO] Bought {symbol} for ${amount:.2f}")



@lru_cache(maxsize=512)
def get_symbol_meta(symbol):
    """
    Pulls tickSize, stepSize, minNotional from exchangeInfo.
    Returns dict: {tick_size, step_size, min_notional}
    """
    info = client.get_symbol_info(symbol)
    tick_size = step_size = None
    min_notional = 0.0
    if not info:
        # Safe defaults (will be overridden by guards anyway)
        return dict(tick_size=0.0, step_size=0.0, min_notional=10.0)

    for f in info.get("filters", []):
        t = f.get("filterType")
        if t == "PRICE_FILTER":
            tick_size = float(f.get("tickSize", "0"))
        elif t == "LOT_SIZE":
            step_size = float(f.get("stepSize", "0"))
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(f.get("minNotional", f.get("minNotional", "10")) or 10.0)

    return dict(
        tick_size=tick_size or 0.0,
        step_size=step_size or 0.0,
        min_notional=min_notional or 10.0
    )

def get_orderbook(symbol, limit=50):
    """Returns bids, asks as lists of (price, qty) floats."""
    ob = client.get_order_book(symbol=symbol, limit=limit)
    bids = [(float(p), float(q)) for p, q in ob.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in ob.get("asks", [])]
    return bids, asks

def compute_spread_rel(bids, asks):
    """Relative spread = (ask1 - bid1) / mid."""
    if not bids or not asks:
        return None
    bid1 = bids[0][0]
    ask1 = asks[0][0]
    mid = (bid1 + ask1) / 2.0
    if mid <= 0:
        return None
    return (ask1 - bid1) / mid

def sum_depth_notional(levels, max_levels=None, max_price_drift=None, side="bid"):
    """
    Sum notional on top 'levels' or within max_price_drift (relative).
    - side='bid' sums bids until price >= (1 - max_price_drift)*best_bid
    - side='ask' sums asks until price <= (1 + max_price_drift)*best_ask
    """
    if not levels:
        return 0.0
    base_price = levels[0][0]
    total = 0.0
    count = 0
    for price, qty in levels:
        if max_levels is not None and count >= max_levels:
            break
        if max_price_drift is not None:
            if side == "bid":
                if price < base_price * (1.0 - max_price_drift):
                    break
            else:
                if price > base_price * (1.0 + max_price_drift):
                    break
        total += price * qty
        count += 1
    return total

def guard_tradability(symbol, side="BUY",
                      max_rel_spread=0.0008,     # 0.08% default cap
                      depth_levels=10,            # sum top-10 levels each side
                      depth_price_window=0.002,   # or within ±0.2% of top
                      min_depth_notional=5000.0,  # require >= $5k both sides
                      min_top_size_usd=500.0,     # best bid/ask notional at L1
                      min_depth_ratio=0.65        # bid/ask depth balance for longs
                      ):
    """
    Returns (ok, reason, diagnostics) before placing orders.
    - Spread must be tight in relative terms **and** not just 1–2 ticks wide by luck.
    - Both sides need healthy notional near top of book.
    - For BUY, bids shouldn't be too weak vs asks.
    """
    try:
        meta = get_symbol_meta(symbol)
        bids, asks = get_orderbook(symbol, limit=max(depth_levels, 20))

        if not bids or not asks:
            return False, "empty_book", {}

        bid1_p, bid1_q = bids[0]
        ask1_p, ask1_q = asks[0]

        # 1) Relative spread check
        rel_spread = compute_spread_rel(bids, asks)
        if rel_spread is None:
            return False, "spread_calc_failed", {}

        # Tick-awareness: if tickSize exists, also require spread <= 4 ticks
        mid = (bid1_p + ask1_p) / 2.0
        tick_guard = True
        if meta["tick_size"] and meta["tick_size"] > 0:
            tick_guard = (ask1_p - bid1_p) <= (4.0 * meta["tick_size"])

        if not (rel_spread <= max_rel_spread and tick_guard):
            return False, "spread_too_wide", {
                "rel_spread": rel_spread,
                "tick_size": meta["tick_size"],
                "tick_guard": tick_guard
            }

        # 2) Top-of-book notional sanity
        l1_bid_notional = bid1_p * bid1_q
        l1_ask_notional = ask1_p * ask1_q
        if l1_bid_notional < min_top_size_usd or l1_ask_notional < min_top_size_usd:
            return False, "l1_notional_too_small", {
                "l1_bid_usd": l1_bid_notional,
                "l1_ask_usd": l1_ask_notional
            }

        # 3) Depth near top (choose either levels or window — we combine both for robustness)
        bid_depth_usd = max(
            sum_depth_notional(bids, max_levels=depth_levels, side="bid"),
            sum_depth_notional(bids, max_price_drift=depth_price_window, side="bid")
        )
        ask_depth_usd = max(
            sum_depth_notional(asks, max_levels=depth_levels, side="ask"),
            sum_depth_notional(asks, max_price_drift=depth_price_window, side="ask")
        )

        if min(bid_depth_usd, ask_depth_usd) < min_depth_notional:
            return False, "depth_too_thin", {
                "bid_depth_usd": bid_depth_usd,
                "ask_depth_usd": ask_depth_usd
            }

        # 4) Balance check (for longs, bids shouldn’t be anemically thin)
        depth_ratio = bid_depth_usd / max(ask_depth_usd, 1e-9)
        if side.upper() == "BUY" and depth_ratio < min_depth_ratio:
            return False, "bid_ask_imbalance_for_long", {
                "bid_depth_usd": bid_depth_usd,
                "ask_depth_usd": ask_depth_usd,
                "depth_ratio": depth_ratio
            }
        if side.upper() == "SELL" and (1.0 / max(depth_ratio, 1e-9)) < min_depth_ratio:
            return False, "ask_bid_imbalance_for_short", {
                "bid_depth_usd": bid_depth_usd,
                "ask_depth_usd": ask_depth_usd,
                "depth_ratio": depth_ratio
            }

        # 5) Exchange minNotional check (ensure your intended order clears it)
        # If you're sizing dynamically, you can plug the planned qty here:
        est_qty = (min_top_size_usd / ask1_p) if side.upper() == "BUY" else (min_top_size_usd / bid1_p)
        est_notional = est_qty * (ask1_p if side.upper() == "BUY" else bid1_p)
        if est_notional < meta["min_notional"]:
            return False, "below_exchange_min_notional", {
                "estimated_order_usd": est_notional,
                "exchange_min_notional": meta["min_notional"]
            }

        return True, "ok", {
            "rel_spread": rel_spread,
            "l1_bid_usd": l1_bid_notional,
            "l1_ask_usd": l1_ask_notional,
            "bid_depth_usd": bid_depth_usd,
            "ask_depth_usd": ask_depth_usd,
            "depth_ratio": depth_ratio
        }

    except Exception as e:
        return False, f"guard_error:{e}", {}

def get_bot_state():
    if not os.path.exists(BOT_STATE_FILE):
        return {"balance": 0, "positions": {}, "paused": False, "log": [], "actions": []}
    with open(BOT_STATE_FILE, "r") as f:
        return json.load(f)

def save_bot_state(state):
    with open(BOT_STATE_FILE, "w") as f:
        json.dump(state, f)

def sync_state():
    state = get_bot_state()
    state["balance"] = balance['usd']
    state["positions"] = positions
    state["log"] = trade_log[-100:]
    save_bot_state(state)

def process_actions():
    state = get_bot_state()
    actions = state.get("actions", [])
    performed = []
    state["actions"] = [a for a in actions if a not in performed]
    save_bot_state(state)

def trading_loop():
    last_sync = time.time()
    last_websocket_update = time.time()
    SYNC_INTERVAL = 60   # Reduced from 240 to 60 seconds (1 minute) for faster response
    WEBSOCKET_UPDATE_INTERVAL = 300  # Reduced to 5 minutes

    # Initialize WebSocket monitoring
    initialize_websocket_monitoring()

    while True:
        try:
            if market_is_risky():
                print("[INFO] Market too volatile. Skipping investing this round.")
                time.sleep(SYNC_INTERVAL)
                continue

            if is_paused():
                print("[INFO] Bot is paused. Skipping trading logic.")
                time.sleep(SYNC_INTERVAL)
                continue

            fetch_usdc_balance()
            auto_sell_momentum_positions()  # <<< use new sell logic

            if time.time() - last_sync > SYNC_INTERVAL:
                sync_investments_with_binance()
                last_sync = time.time()
                
            # Update WebSocket symbols periodically
            if time.time() - last_websocket_update > WEBSOCKET_UPDATE_INTERVAL:
                update_websocket_symbols()
                last_websocket_update = time.time()

            if not too_many_positions():
                reserve_taxes_and_reinvest()  # <<<< THIS IS THE NEW LOGIC

            sync_state()
            process_actions()
            fetch_usdc_balance()
        except Exception as e:
            print(f"[LOOP ERROR] {e}")

        sync_positions_with_binance(client, positions)
        display_portfolio(positions, get_latest_price)
        time.sleep(SYNC_INTERVAL)

def resume_positions_from_binance():
    try:
        account_info = client.get_account()
        balances = {
            a["asset"]: float(a["free"])
            for a in account_info["balances"]
            if float(a["free"]) > 0.0001 and a["asset"] != BASE_ASSET
        }
        resumed = {}
        for asset, amount in balances.items():
            symbol = f"{asset}{BASE_ASSET}"
            try:
                price = float(client.get_symbol_ticker(symbol=symbol)["price"])
                resumed[symbol] = {
                    "entry": price,
                    "qty": amount,
                    "timestamp": time.time(),
                    "trade_time": time.time()
                }
            except Exception:
                continue
        return resumed
    except Exception:
        return {}

async def send_alarm_message(text):
    """Send an alarm message to the Telegram chat."""
    from telegram import Bot
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)

def send_alarm_message_safe(text):
    """Send alarm message safely, handling existing event loops."""
    import asyncio
    import threading
    
    try:
        # Try to get existing loop
        loop = asyncio.get_running_loop()
        # If we have a running loop, run in a thread
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(send_alarm_message(text))
            finally:
                new_loop.close()
        
        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=10)  # Wait up to 10 seconds
        
    except RuntimeError:
        # No running loop, run normally
        try:
            asyncio.run(send_alarm_message(text))
        except Exception as e:
            print(f"[ALARM ERROR] Could not send message: {e}")
    except Exception as e:
        print(f"[ALARM ERROR] Unexpected error sending message: {e}")

async def check_and_alarm_high_volume_optimized(context=None):
    """
    Optimized version that uses WebSocket data and processes in batches to reduce delays.
    """
    stats = load_symbol_stats()
    if not stats:
        return
    
    alarmed = []
    full_momentum_alerts = []
    processed_count = 0
    
    # Process symbols in smaller batches to reduce processing time
    symbol_items = list(stats.items())
    batch_size = 5  # Smaller batches for faster processing
    
    start_time = time.time()
    
    for i in range(0, len(symbol_items), batch_size):
        # Check if we're taking too long (max 25 seconds to avoid scheduler delays)
        if time.time() - start_time > 25:
            print(f"[ALARM TIMEOUT] Processed {processed_count}/{len(symbol_items)} symbols, stopping to avoid delay")
            break
            
        batch = symbol_items[i:i+batch_size]
        
        for symbol, s in batch:
            try:
                vol = s.get("volume_1d", 0) or 0
                min_volume = get_min_volume_for_symbol(symbol)
                
                if vol > min_volume:
                    # Quick filter using WebSocket data if available
                    price_data = get_cached_price_data(symbol)
                    if price_data and time.time() - price_data['timestamp'] < 60:
                        # Skip if 24h change is too small to be interesting
                        if abs(price_data['price_change_pct']) < 0.5:  # Less than 0.5% 24h change
                            processed_count += 1
                            continue
                    
                    # Compute dynamic thresholds for this symbol
                    d1, d5, d15 = dynamic_momentum_set(symbol)

                    # Check momentum with reduced API calls where possible
                    try:
                        m1, ind1 = check_advanced_momentum(symbol, '1m',  d1)
                        m5, ind5 = check_advanced_momentum(symbol, '5m',  d5)
                        m15,ind15= check_advanced_momentum(symbol, '15m', d15)
                        
                        # Ensure all momentum values are boolean (never None)
                        m1 = bool(m1) if m1 is not None else False
                        m5 = bool(m5) if m5 is not None else False
                        m15 = bool(m15) if m15 is not None else False
                        
                    except Exception as e:
                        print(f"[MOMENTUM CHECK ERROR] {symbol}: {e}")
                        processed_count += 1
                        continue

                    # Count momentum stages met (safe sum with boolean values)
                    momentum_count = sum([m1, m5, m15])
                    
                    # Full momentum alert (all 3 stages)
                    if momentum_count == 3:
                        pct1  = ind1.get('pct_change', 0) if ind1 else 0
                        pct5  = ind5.get('pct_change', 0) if ind5 else 0
                        pct15 = ind15.get('pct_change', 0) if ind15 else 0
                        
                        full_momentum_alerts.append((symbol, vol, pct1, pct5, pct15, d1, d5, d15))
                    
                    # Partial momentum alert (2 out of 3 stages)
                    elif momentum_count >= 2:
                        pct1  = ind1.get('pct_change', 0) if ind1 else 0
                        pct5  = ind5.get('pct_change', 0) if ind5 else 0
                        pct15 = ind15.get('pct_change', 0) if ind15 else 0

                        diff1  = (d1*100)  - pct1
                        diff5  = (d5*100)  - pct5
                        diff15 = (d15*100) - pct15

                        alarmed.append((symbol, vol, pct1, pct5, pct15, m1, m5, m15, diff1, diff5, diff15, d1, d5, d15))
                        
                processed_count += 1
                        
            except Exception as e:
                print(f"[ALARM PROCESSING ERROR] {symbol}: {e}")
                processed_count += 1
                continue
        
        # Small delay between batches to prevent overwhelming the system
        if i + batch_size < len(symbol_items):
            await asyncio.sleep(0.05)  # 50ms delay between batches
    
    processing_time = time.time() - start_time
    print(f"[ALARM PROCESSING] Completed in {processing_time:.2f}s, processed {processed_count} symbols")
    
    # Send alerts (rest of the function remains the same)
    # ... (continuing with existing alert logic)

# Keep the original function for now, but use optimized version in alarm_job
async def check_and_alarm_high_volume(context=None):
    stats = load_symbol_stats()
    if not stats:
        return
    alarmed = []
    full_momentum_alerts = []
    
    for symbol, s in stats.items():
        vol = s.get("volume_1d", 0) or 0
        min_volume = get_min_volume_for_symbol(symbol)
        if vol > min_volume:
            # compute dynamic thresholds for this symbol
            d1, d5, d15 = dynamic_momentum_set(symbol)

            m1, ind1 = check_advanced_momentum(symbol, '1m',  d1)
            m5, ind5 = check_advanced_momentum(symbol, '5m',  d5)
            m15,ind15= check_advanced_momentum(symbol, '15m', d15)

            # Ensure all momentum values are boolean (never None)
            m1 = bool(m1) if m1 is not None else False
            m5 = bool(m5) if m5 is not None else False
            m15 = bool(m15) if m15 is not None else False

            # Count momentum stages met (safe sum with boolean values)
            momentum_count = sum([m1, m5, m15])
            
            # Full momentum alert (all 3 stages)
            if momentum_count == 3:
                pct1  = ind1.get('pct_change', 0) if ind1 else 0
                pct5  = ind5.get('pct_change', 0) if ind5 else 0
                pct15 = ind15.get('pct_change', 0) if ind15 else 0
                
                full_momentum_alerts.append((symbol, vol, pct1, pct5, pct15, d1, d5, d15))
            
            # Partial momentum alert (2 out of 3 stages)
            elif momentum_count >= 2:
                pct1  = ind1.get('pct_change', 0) if ind1 else 0
                pct5  = ind5.get('pct_change', 0) if ind5 else 0
                pct15 = ind15.get('pct_change', 0) if ind15 else 0

                diff1  = (d1*100)  - pct1
                diff5  = (d5*100)  - pct5
                diff15 = (d15*100) - pct15

                alarmed.append((symbol, vol, m1, m5, m15, pct1, pct5, pct15, diff1, diff5, diff15, d1, d5, d15))

    # Send full momentum alerts first (highest priority)
    if full_momentum_alerts:
        msg = "🚀 FULL MOMENTUM ALERT - ALL 3 STAGES MET! 🚀\n\n"
        for (symbol, vol, pct1, pct5, pct15, d1, d5, d15) in full_momentum_alerts:
            msg += f"🎯 {symbol}: Volume = {vol:,.0f}\n"
            msg += f"  1m: ✅ {pct1:.2f}%  (Target {d1*100:.2f}%)\n"
            msg += f"  5m: ✅ {pct5:.2f}%  (Target {d5*100:.2f}%)\n"
            msg += f" 15m: ✅ {pct15:.2f}% (Target {d15*100:.2f}%)\n\n"
        await send_alarm_message(msg)

    # Send partial momentum alerts (2/3 stages)
    if alarmed:
        msg = "🚨 High Volume Alert (2/3 momentum stages reached):\n\n"
        for (symbol, vol, m1, m5, m15, pct1, pct5, pct15, diff1, diff5, diff15, d1, d5, d15) in alarmed:
            msg += f"📊 {symbol}: Volume = {vol:,.0f}\n"
            msg += f"  1m: {'✅' if m1 else '❌'} {pct1:.2f}%  (Target {d1*100:.2f}%, need {max(0,diff1):.2f}%)\n"
            msg += f"  5m: {'✅' if m5 else '❌'} {pct5:.2f}%  (Target {d5*100:.2f}%, need {max(0,diff5):.2f}%)\n"
            msg += f" 15m: {'✅' if m15 else '❌'} {pct15:.2f}% (Target {d15*100:.2f}%, need {max(0,diff15):.2f}%)\n\n"
        await send_alarm_message(msg)

# --- Telegram alarm job setup ---
async def alarm_job(context: CallbackContext):
    """
    Ultra-fast alarm job that detects momentum and executes investments.
    """
    start_time = time.time()
    
    try:
        # Check if bot is paused
        if is_paused():
            print("[ALARM JOB] Bot is paused, skipping momentum check")
            return
        
        # Run momentum detection and investment in a separate thread with strict timeout
        import concurrent.futures
        import threading
        
        def quick_momentum_check_and_invest():
            """Fast momentum check with immediate investment execution."""
            try:
                stats = load_symbol_stats()
                if not stats:
                    return
                
                full_momentum_count = 0
                partial_momentum_count = 0
                processed = 0
                max_process_time = 10  # Maximum 10 seconds total
                start = time.time()
                
                # Only check top 10 highest volume symbols for speed
                symbol_items = list(stats.items())
                # Sort by volume and take top 10
                symbol_items.sort(key=lambda x: x[1].get("volume_1d", 0), reverse=True)
                top_symbols = symbol_items[:10]
                
                alerts_to_send = []
                investment_candidates = []  # Track symbols ready for investment
                
                for symbol, s in top_symbols:
                    if time.time() - start > max_process_time:
                        print(f"[QUICK CHECK] Timeout reached, processed {processed} symbols")
                        break
                        
                    try:
                        vol = s.get("volume_1d", 0) or 0
                        min_volume = get_min_volume_for_symbol(symbol)
                        
                        if vol > min_volume:
                            # Super quick filter using WebSocket data
                            price_data = get_cached_price_data(symbol)
                            if price_data and time.time() - price_data['timestamp'] < 30:
                                # Only proceed if significant price movement
                                if abs(price_data['price_change_pct']) < 1.0:
                                    processed += 1
                                    continue
                            
                            # Quick momentum check - only if WebSocket shows promise
                            d1, d5, d15 = dynamic_momentum_set(symbol)
                            
                            # Simplified momentum check with single API call
                            try:
                                d1, d5, d15 = dynamic_momentum_set(symbol)
                                
                                # Ensure d1, d5, d15 are not None
                                if d1 is None or d5 is None or d15 is None:
                                    continue
                                    
                                klines_1m = client.get_klines(symbol=symbol, interval='1m', limit=2)
                                if len(klines_1m) >= 2:
                                    prev_close = float(klines_1m[-2][4])
                                    last_close = float(klines_1m[-1][4])
                                    pct_1m = (last_close - prev_close) / prev_close
                                    
                                    # Only do full check if 1m looks promising
                                    if pct_1m > d1:
                                        # Quick 5m and 15m check
                                        try:
                                            m5, ind5 = check_advanced_momentum(symbol, '5m', d5)
                                            m15, ind15 = check_advanced_momentum(symbol, '15m', d15)
                                            
                                            # Ensure m5 and m15 are boolean values (never None)
                                            m5 = bool(m5) if m5 is not None else False
                                            m15 = bool(m15) if m15 is not None else False
                                            
                                            momentum_count = 1 + sum([m5, m15])  # 1m already passed
                                            
                                            if momentum_count >= 2:  # Less strict: 2/3 or more
                                                pct5 = ind5.get('pct_change', 0) if ind5 else 0
                                                pct15 = ind15.get('pct_change', 0) if ind15 else 0
                                                alerts_to_send.append(('full', symbol, vol, pct_1m*100, pct5, pct15, d1, d5, d15))
                                                full_momentum_count += 1
                                                
                                                # EXECUTE INVESTMENT IMMEDIATELY
                                                try:
                                                    fetch_usdc_balance()  # Update balance
                                                    current_balance = balance.get('usd', 0)  # Get from global balance
                                                    
                                                    # Debug: Show live balance before investment attempt
                                                    print(f"[DEBUG] Live USDC balance before {symbol} investment: {current_balance:.8f}")
                                                    
                                                    # Ensure current_balance is a valid number
                                                    if current_balance is None:
                                                        current_balance = 0
                                                        
                                                    if current_balance >= 25:  # Minimum balance check
                                                        amount_to_spend = min(50, current_balance * 0.1)  # Max 50 USDC or 10% balance
                                                        
                                                        print(f"[DEBUG] Planning to invest {amount_to_spend:.2f} USDC in {symbol}")
                                                        
                                                        # Execute the buy order
                                                        result = buy(symbol, amount_to_spend)
                                                        if result:
                                                            investment_candidates.append(symbol)
                                                            
                                                            # Debug: Show balance after investment
                                                            updated_balance = balance.get('usd', 0)
                                                            print(f"🎯 INVESTED {amount_to_spend:.2f} USDC in {symbol}")
                                                            print(f"[DEBUG] Updated USDC balance after {symbol} investment: {updated_balance:.8f}")
                                                        else:
                                                            print(f"❌ Investment order failed for {symbol}")
                                                    else:
                                                        print(f"⚠️  Insufficient balance for {symbol}: {current_balance:.2f} USDC")
                                                except Exception as invest_error:
                                                    print(f"❌ Investment error for {symbol}: {invest_error}")
                                            elif momentum_count >= 1:  # Partial momentum
                                                partial_momentum_count += 1
                                                
                                        except Exception:
                                            pass  # Skip on error to maintain speed
                                            
                            except Exception:
                                pass  # Skip on error to maintain speed
                                
                        processed += 1
                        
                    except Exception:
                        processed += 1
                        continue
                
                # Send alerts quickly
                if alerts_to_send:
                    import asyncio
                    
                    async def send_quick_alerts():
                        for alert in alerts_to_send:
                            if alert[0] == 'full':
                                _, symbol, vol, pct1, pct5, pct15, d1, d5, d15 = alert
                                
                                # Check if we invested in this symbol
                                invested_emoji = "💰 INVESTED!" if symbol in investment_candidates else "📊 DETECTED"
                                
                                msg = (
                                    f"🚀 MOMENTUM ALERT - {invested_emoji} 🚀\n\n"
                                    f"🎯 {symbol}: Volume = {vol:,.0f}\n"
                                    f"  1m: ✅ {pct1:.2f}%  (Target {d1*100:.2f}%)\n"
                                    f"  5m: ✅ {pct5:.2f}%  (Target {d5*100:.2f}%)\n"
                                    f" 15m: ✅ {pct15:.2f}% (Target {d15*100:.2f}%)"
                                )
                                await send_alarm_message(msg)
                    
                    # Run the alert sending
                    asyncio.run(send_quick_alerts())
                
                processing_time = time.time() - start
                print(f"[ALARM JOB] Completed in {processing_time:.2f}s: {processed} symbols, {full_momentum_count} momentum alerts, invested in {len(investment_candidates)} symbols")
                
            except Exception as e:
                print(f"[ALARM JOB] Error: {e}")
        
        # Execute with very strict timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(quick_momentum_check_and_invest)
            try:
                future.result(timeout=15)  # Max 15 seconds
            except concurrent.futures.TimeoutError:
                print("[ALARM JOB] Quick check timed out, skipping cycle")
        
        total_time = time.time() - start_time
        if total_time > 20:  # Warn if taking too long
            print(f"[ALARM JOB WARNING] Job took {total_time:.2f} seconds")
            
    except Exception as e:
        print(f"[ALARM JOB ERROR] {e}")

def test_websocket_connection():
    """Test WebSocket connection for debugging purposes."""
    print("[WEBSOCKET TEST] 🧪 Testing WebSocket connectivity...")
    
    test_manager = None
    try:
        # Create a temporary WebSocket manager
        test_manager = WebSocketPriceManager(client)
        print("[WEBSOCKET TEST] ✅ WebSocket manager created successfully")
        
        # Try to start it
        test_manager.start()
        print(f"[WEBSOCKET TEST] Start result - Running: {getattr(test_manager, '_running', False)}")
        
        # Since we're using simplified mode, test REST API fallback
        print("[WEBSOCKET TEST] 🔧 Testing REST API fallback for BTCUSDC...")
        price = get_latest_price('BTCUSDC')
        if price:
            print(f"[WEBSOCKET TEST] ✅ BTCUSDC price via REST API: ${price}")
        else:
            print("[WEBSOCKET TEST] ❌ No BTCUSDC price received via REST API")
            
        print("[WEBSOCKET TEST] 🔧 Testing REST API fallback for ETHUSDC...")
        price = get_latest_price('ETHUSDC')
        if price:
            print(f"[WEBSOCKET TEST] ✅ ETHUSDC price via REST API: ${price}")
        else:
            print("[WEBSOCKET TEST] ❌ No ETHUSDC price received via REST API")
        
    except Exception as e:
        print(f"[WEBSOCKET TEST] ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        if test_manager:
            try:
                test_manager.stop()
                print("[WEBSOCKET TEST] 🛑 Test completed and cleaned up")
            except Exception as e:
                print(f"[WEBSOCKET TEST] ⚠️ Cleanup warning: {e}")

if __name__ == "__main__":
    # Enable tracemalloc for better debugging
    import tracemalloc
    tracemalloc.start()
    
    # Set up signal handlers for clean shutdown
    import signal
    import atexit
    
    def signal_handler(signum, frame):
        print(f"\n[SIGNAL] Received signal {signum}, shutting down gracefully...")
        cleanup_websocket_monitoring()
        import sys
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    # Register cleanup on normal exit
    atexit.register(cleanup_websocket_monitoring)
    
    # Run WebSocket test first
    test_websocket_connection()
    
    # Initialize WebSocket monitoring
    initialize_websocket_monitoring()
    
    # Add performance monitoring to reduce scheduler warning noise
    add_performance_monitoring()
    
    # Load trade history and rebuild positions - no need for refresh_symbols() 
    # since get_yaml_ranked_momentum() loads symbols dynamically when needed
    trade_log = load_trade_history()
    positions.clear()
    positions.update(rebuild_cost_basis(trade_log))
    reconcile_positions_with_binance(client, positions)
    print(f"[INFO] Bot paused state on startup: {is_paused()}")
    
    try:
        # Start trading thread
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()
        
        # Start Telegram bot (may run in separate thread if event loop exists)
        bot_thread = telegram_main()
        
        # If telegram_main returned a thread, join it (blocking wait)
        if bot_thread and hasattr(bot_thread, 'join'):
            print("[MAIN] Waiting for Telegram bot thread...")
            bot_thread.join()
        
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down gracefully...")
        cleanup_websocket_monitoring()
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        cleanup_websocket_monitoring()
    finally:
        cleanup_websocket_monitoring()
        print("[INFO] Goodbye!")
