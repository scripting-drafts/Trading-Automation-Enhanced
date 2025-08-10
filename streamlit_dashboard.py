# streamlit_dashboard.py
import json
import streamlit as st
import time

st.set_page_config(page_title="Crypto Bot Dashboard", layout="wide")

st.title("Crypto Bot Dashboard")

def load_state():
    try:
        with open("bot_state.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"balance": 0, "positions": {}}

state = load_state()
last_update = time.strftime("%Y-%m-%d %H:%M:%S")

st.subheader(f"USDC Balance: ${state.get('balance', 0):,.2f}")
positions = state.get("positions", {})
if positions:
    st.subheader("Open Positions")
    data = []
    for sym, pos in positions.items():
        data.append({
            "Symbol": sym,
            "Qty": pos.get("qty", 0),
            "Entry": pos.get("entry", 0),
            "Timestamp": pos.get("timestamp", 0),
        })
    st.table(data)
else:
    st.write("No open positions.")
st.caption(f"Last updated: {last_update}")
