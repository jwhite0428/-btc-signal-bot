# Kalshi BTC 15-Minute Paper Trader v5

## Install on Streamlit Community Cloud
Replace `app.py` and `requirements.txt` in your existing GitHub repository. Commit both files. Streamlit Cloud will redeploy automatically. The app uses public Kalshi `KXBTC15M` market endpoints and Coinbase BTC candles. No API key or real-money trading.

## Features
- Auto-selects the soonest closing open `KXBTC15M` market.
- Reads actual Kalshi YES/NO bid and ask prices (with order book fallback).
- Manual simulated YES/NO buys and sells.
- Optional automatic simulated entry based on a simple EMA/RSI/momentum rule.
- Simulated take-profit and stop-loss exits on refresh using available bids.
- SQLite-backed paper positions, trade history, and fee estimates.

## Limitations
- Auto trades and exits only happen while the Streamlit session is awake and refreshing. This is **not** a 24/7 background bot.
- SQLite storage on Streamlit Community Cloud is ephemeral and can reset on app restart/redeploy. Do not treat it as a permanent trade ledger.
- No automatic settlement accounting; expired positions may remain open until you address them manually or a later release adds official result reconciliation.
- Simulated execution ignores latency, queue position, depth, and partial fills. Fees are estimates, not official charged fees.
- Do not use these signals as guarantees or calibrated probabilities.

## First test
1. Select Manual mode and 1–5 contracts.
2. Buy a paper YES or NO contract with the displayed ask.
3. Confirm it appears under Open positions.
4. Watch the bid and exit manually or when a take-profit/stop-loss threshold is reached on a refresh.
5. Switch to Auto paper, enable the entry toggle, and watch the app while open.
