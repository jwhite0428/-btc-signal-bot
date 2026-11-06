# SENTINEL v7 — Selective Paper Strategy

Keeps the dark SENTINEL dashboard and Kalshi 15-minute quote feed.

## New selective filters
- Default 10/13 directional votes, lead >= 5, ADX >= 20
- Maximum contract ask 65 cents and maximum spread 12 cents
- At least 180 seconds remaining
- One automatic paper entry per interval
- Filters displayed in HOLD / WAIT status
- Closed paper trade win rate, net P/L, profit factor and downloadable CSV logs
- Manual paper entry remains possible as an override

## Limitations
This is a forward paper-trading prototype, not a validated strategy. Indicator alignment is not a calibrated probability. No real-money orders. No historical backtest, no authoritative settlement of expired contracts, no persistent storage, no unattended 24/7 engine. Quotes and fills are simplified and may not be available.

Replace app.py and requirements.txt in the root of your GitHub repository and redeploy Streamlit.
