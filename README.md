# SENTINEL BTC 15M — Clear Actions

Adds clear ENTER NOW / HOLD / WAIT / TAKE PROFIT / EXIT banners based on current Kalshi YES/NO contract ask and bid prices, while keeping the visual Sentinel dashboard and manual/automatic paper modes.

- ENTER NOW appears only when a strong signal exists, a live contract ask is available and within your configured max entry, sufficient simulated cash exists, and the market is not too close to closing. It is a potential entry, **not** proof of a fill.
- HOLD / WAIT shows position entry, current bid, take-profit and stop-loss target, or why a new entry is unavailable.
- TAKE PROFIT / EXIT and STOP LOSS / EXIT are shown when a simulated exit occurs during a refresh. Trade history records the paper exit.
- Take-profit and stop-loss settings are saved on each position at entry, so changing the sidebar settings does not retroactively change an existing position.
- Opposing signal stops now compare UP with YES and DOWN with NO correctly.

## Installation
Upload `app.py` and `requirements.txt` into the **root** of your existing GitHub repository (not inside a folder), commit changes, and allow Streamlit Cloud to redeploy. Streamlit main module should be `app.py`.

## Limitations
Paper-only. Quotes are based on public Kalshi orderbook and BTC chart is a separate public spot feed for technical analysis. Streamlit must remain running/refreshing to evaluate simulated exits; it does not send reliable background phone alerts. No automatic settlement for expired positions. Fees and fills are estimates; no real orders, and signal alignment is not a calibrated probability of winning.
