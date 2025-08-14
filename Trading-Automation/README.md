# High Risk Auto Trade
  
| File Name                  | Description                                  |
|----------------------------|----------------------------------------------|
| high_risk_autotrade.py     | Main bot (do NOT import/run in Streamlit!)   |
| streamlit_dashboard.py     | Dashboard ONLY                               |
| secret.py                  | API Keys                                     |
| bot_state.json             | Shared state file                            |
| usdc_symbol_updater.py     | Fetches USDC crypto state                    |
| symbols.yaml               | USDC cryptos state                           |

  <br /> 
Requirements:  
pip install python-telegram-bot==22.2 (Previous 13.5)  
sudo apt install ntpdate  
sudo ntpdate pool.ntp.org  
  <br />  

### How does it work
 - Generate symbols.yaml  
 - Run the bot  
 - Keep usdc_symbol_updater on  
  <br />

## Momentum Detection  
The function has_recent_momentum() requires positive price changes on 1m, 5m, and 15m candles (default: +0.3%, +0.6%, +1.0%).
  <br />

## Entry  
The bot will consider buying only if all these timeframes show strong upward movement. This is like what’s shown on the chart—buying during the clear uptrend.
  <br />

## Auto-Sell  
The bot uses both trailing stop logic and a maximum hold time, so if the price reverses sharply (like those red candles after the top in the image), the bot should try to exit quickly.
  <br />

![Example](https://i.imgur.com/7oMaPLM.jpeg)  


### Summary

If the trend is very short (just 2-3 candles), the bot might enter late or get faked out.  
  <br />
If a strong reversal happens quickly (as in the sharp red drop), the trailing stop might trigger and exit, but very fast drops can result in some slippage.  


### Tuning tips (micro-scalping)

Start with k≈0.55–0.65 for 1m, 0.7–0.8 for 5m, 0.85–1.0 for 15m.

If you’re getting too many entries, raise k or the floor_.

If you miss small but clean moves, lower k slightly on 1m or 5m.

Keep cap_ ≤ 2% so you don’t wait forever in ultra-volatile spikes.

Your sell side (trailing stop / min profit) is already in place; you can consider reducing MIN_PROFIT a touch (e.g., 0.8%) and tightening TRAIL_STOP (e.g., 0.5%) if fills are fast and spreads are tight. 