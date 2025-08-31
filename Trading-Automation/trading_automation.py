import yaml, pytz, json, os, csv, decimal, time, threading, math, random
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException
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
from secrets import API_KEY, API_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
# API_KEY = os.environ['BINANCE_KEY']
# API_SECRET = os.environ['BINANCE_SECRET']
# TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
# TELEGRAM_CHAT_ID = int(os.environ['TELEGRAM_CHAT_ID'])

def patched_get_localzone():
    return pytz.UTC

apscheduler.util.get_localzone = patched_get_localzone

BASE_ASSET = 'USDC'
DUST_LIMIT = 0.4

MIN_MARKETCAP = 69_502  
MIN_VOLUME = 300_000
MIN_VOLATILITY = 0.04

MIN_1M = 0.005   # 0.5% in 1m
MIN_5M = 0.01   # 1% in 5m
MIN_15M = 0.02   # 2% in 15m

# New indicator thresholds
RSI_OVERBOUGHT = 70
VOLUME_SPIKE_MULTIPLIER = 2.0  # 2x average volume
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
balance = {'usd': 0.0}
trade_log = []

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

def fetch_usdc_balance():
    """Update the global USDC balance from Binance live."""
    try:
        asset_info = client.get_asset_balance(asset="USDC")
        free = float(asset_info['free'])
        balance['usd'] = free
        print(f"[DEBUG] Live USDC balance: {free}")
    except Exception as e:
        print(f"[ERROR] Fetching USDC balance: {e}")
        balance['usd'] = 0

def get_latest_price(symbol):
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except Exception as e:
        print(f"[PRICE ERROR] {symbol}: {e}")
        return None

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

    if text == "📊 Balance":
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


    
    elif text == "💼 Investments":
        msg = format_investments_message(positions, get_latest_price, DUST_LIMIT)
        await send_with_keyboard(update, msg)
    
    elif text == "⏸ Pause Trading":
        set_paused(True)
        await send_with_keyboard(update, "⏸ Trading is now *paused*. Bot will not auto-invest or auto-sell until resumed.", parse_mode='Markdown')

    elif text == "▶️ Resume Trading":
        set_paused(False)
        await send_with_keyboard(update, "▶️ Trading is *resumed*. Bot will continue auto-investing and auto-selling.", parse_mode='Markdown')

    elif text == "📝 Trade Log":
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
    else:
        await send_with_keyboard(update, "Unknown action.")

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
    ["📝 Trade Log"]
]

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_with_keyboard(
        update,
        "Welcome! Use the buttons below\n",
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )

def telegram_main():
    application = ApplicationBuilder() \
        .token(TELEGRAM_TOKEN) \
        .build()

    application.add_handler(CommandHandler('start', start_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_handle_message))

    # Add periodic alarm job (every 5 minutes)
    application.job_queue.run_repeating(alarm_job, interval=300, first=10)

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
    application.run_polling()


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

def buy(symbol, amount=None):
    print(f"[DEBUG] Actual USDC balance before buy: {balance['usd']}")
    try:
        precision = quote_precision_for(symbol)
        trade_amount = round(amount, precision)
        min_notional = min_notional_for(symbol)
        print(f"[DEBUG] Attempting to buy {symbol}: trade_amount=${trade_amount}, min_notional=${min_notional}")
        if trade_amount < min_notional:
            print(f"[SKIP] {symbol}: Trade amount (${trade_amount}) < MIN_NOTIONAL (${min_notional})")
            return None
        # Let Binance handle rounding/fees
        order = client.order_market_buy(symbol=symbol, quoteOrderQty=trade_amount)
        price = float(order['fills'][0]['price'])
        qty = float(order['executedQty'])
        qty = round_qty(symbol, qty)
        print(f"[INFO] Bought {symbol}: qty={qty}, price={price}")
        balance['usd'] -= trade_amount
        positions[symbol] = {
            'entry': price,
            'qty': qty,
            'timestamp': time.time(),
            'trade_time': time.time()
        }
        print(f"[DEBUG] Actual USDC balance after buy attempt: {balance['usd']}")
        
        # Send investment alert
        import asyncio
        try:
            asyncio.create_task(send_alarm_message(
                f"💰 INVESTMENT MADE\n\n"
                f"📊 {symbol}\n"
                f"💵 Amount: ${trade_amount:.2f}\n"
                f"📈 Price: ${price:.6f}\n"
                f"🔢 Quantity: {qty:.6f}\n"
                f"💼 Remaining USDC: ${balance['usd']:.2f}"
            ))
        except Exception as e:
            print(f"[ALERT ERROR] Could not send investment alert: {e}")
        
        return positions[symbol]
    except BinanceAPIException as e:
        print(f"[BUY ERROR] {symbol}: {e}")
        return None

def sell(symbol, qty):
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
                    import asyncio
                    try:
                        asyncio.create_task(send_alarm_message(
                            f"💸 POSITION SOLD\n\n"
                            f"📊 {symbol}\n"
                            f"📉 Entry: ${entry:.6f}\n"
                            f"📈 Exit: ${exit_price:.6f}\n"
                            f"🔢 Quantity: {qty:.6f}\n"
                            f"💰 PnL: ${pnl_dollar:.2f} ({pnl_pct:.2f}%)\n"
                            f"⏱️ Hold Time: {held_for/60:.1f} min\n"
                            f"📋 Reason: {reason}"
                        ))
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

    invest_momentum_with_usdc_limit(investable_usdc)

def load_symbol_stats():
    try:
        with open(YAML_SYMBOLS_FILE, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"[YAML ERROR] Could not read {YAML_SYMBOLS_FILE}: {e}")
        return {}
    
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

def check_advanced_momentum(symbol, interval='1m', min_change=0.005):
    """Check momentum using price change, RSI, volume spike, and MA cross."""
    try:
        # Get price change
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
            'rsi_momentum': rsi_momentum
        }
        
        # Require price momentum + at least one other indicator
        has_momentum = price_momentum and (volume_spike or rsi_momentum or ma_cross)
        
        return has_momentum, indicators
    except Exception as e:
        print(f"[ADVANCED MOMENTUM ERROR] {symbol} {interval}: {e}")
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
    Produce per-interval dynamic thresholds tuned for micro-scalping.
    We make shorter TFs a bit more sensitive (lower k), longer TFs stricter (higher k).
    """
    thr_1m  = dynamic_momentum_threshold(symbol, '1m',  lookback=60,  k=0.55, floor_=0.0007, cap_=0.015)
    thr_5m  = dynamic_momentum_threshold(symbol, '5m',  lookback=48,  k=0.70, floor_=0.0010, cap_=0.018)
    thr_15m = dynamic_momentum_threshold(symbol, '15m', lookback=48,  k=0.85, floor_=0.0015, cap_=0.020)
    return thr_1m, thr_5m, thr_15m


def has_recent_momentum(symbol, min_1m=None, min_5m=None, min_15m=None):
    """Enhanced momentum detection using multiple indicators with dynamic thresholds."""
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

        # Keep your strong confirmation rule (all TFs) for now; you can relax if fills are sparse
        return momentum_1m and momentum_5m and momentum_15m

    except Exception as e:
        print(f"[MOMENTUM ERROR] {symbol}: {e}")
        return False


def get_yaml_ranked_momentum(
        limit=MAX_POSITIONS, 
        min_marketcap=MIN_MARKETCAP, 
        min_volume=MIN_VOLUME, 
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

def refresh_symbols():
    global SYMBOLS
    SYMBOLS = get_yaml_ranked_momentum(limit=10)

def invest_momentum_with_usdc_limit(usdc_limit):
    """
    Invest in as many eligible momentum symbols as possible, always using the min_notional per symbol,
    never all-or-nothing. Any remaining funds are left in USDC.
    This version treats coins you own (including dust) and coins you don't equally.
    """
    refresh_symbols()
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
    SYNC_INTERVAL = 180

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

async def check_and_alarm_high_volume(context=None):
    stats = load_symbol_stats()
    if not stats:
        return
    alarmed = []
    full_momentum_alerts = []
    
    for symbol, s in stats.items():
        vol = s.get("volume_1d", 0) or 0
        if vol > MIN_VOLUME:
            # compute dynamic thresholds for this symbol
            d1, d5, d15 = dynamic_momentum_set(symbol)

            m1, ind1 = check_advanced_momentum(symbol, '1m',  d1)
            m5, ind5 = check_advanced_momentum(symbol, '5m',  d5)
            m15,ind15= check_advanced_momentum(symbol, '15m', d15)

            # Count momentum stages met
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
    await check_and_alarm_high_volume(context)

if __name__ == "__main__":
    refresh_symbols()
    trade_log = load_trade_history()
    positions.clear()
    positions.update(rebuild_cost_basis(trade_log))
    reconcile_positions_with_binance(client, positions)
    print(f"[INFO] Bot paused state on startup: {is_paused()}")
    
    try:
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()
        telegram_main()  # This blocks; run in main thread for proper Ctrl+C
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down gracefully...")
    finally:
        print("[INFO] Goodbye!")
