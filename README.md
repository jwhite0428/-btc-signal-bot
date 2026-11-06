# SENTINEL 24/7 — paper-only cloud worker + original Streamlit dashboard

This is a **paper simulation**, not an exchange-connected trader. There are **no trading API keys or order submission endpoints**. It does not guarantee wins or profit.

## Phone-only deployment (GitHub + Render + Streamlit)

1. Download this ZIP and unzip it in Files on your iPhone.
2. In your GitHub SENTINEL repository, upload **all files from the ZIP's root** (`app.py`, `strategy.py`, `worker.py`, `server.py`, `render.yaml`, `requirements-worker.txt`, `requirements.txt`). Replace existing `app.py` and `requirements.txt`. Keep `README.md` if desired. Commit.
3. Visit https://dashboard.render.com/ and choose **New > Blueprint**, connect the GitHub repo, and deploy `render.yaml`. It requests a **paid Starter web service and a persistent disk**. Verify actual Render pricing and billing before confirming. The Render worker needs a paid always-on instance; free/sleeping services are not sufficient.
4. Render will set a secret `DASHBOARD_TOKEN` automatically. In the Render service Environment page, copy its value. **Never commit it to GitHub or post it in screenshots.**
5. Copy your Render service URL, e.g. `https://YOUR-SERVICE.onrender.com`.
6. In your Streamlit Community Cloud app settings, add **Secrets** (TOML):

```toml
SENTINEL_WORKER_URL = "https://YOUR-SERVICE.onrender.com"
SENTINEL_WORKER_TOKEN = "YOUR-SECRET-TOKEN"
```

7. Restart/refresh Streamlit. The **24/7 WORKER** panel appears near the top of your original dashboard. It shows durable trade history, P/L, intervals and errors. Your original chart and indicator visuals remain.
8. **Use Streamlit only for viewing the worker's paper trades**. The local Streamlit auto-entry toggle is disabled whenever the worker is configured, to prevent duplicate simulated positions. The old session-only trading section is labeled as legacy and is not synchronized with the worker database.

## How the cloud service works

- The web process starts one background worker thread, polling about every 15 seconds. It can run when your phone is off, as long as the paid host is running.
- Data: Coinbase BTC/USD 1-minute candles, Kalshi public KXBTC15M order books. Entry at inferred ask; exit at bid. Requires enough visible size at the best level.
- Current balanced filters: 8/13 directional votes, 4-vote lead, ADX 18, max 60c ask, max 8c spread, at least 120 seconds remaining, 1 paper contract per market, 15c TP, 10c SL. Edit environment variables on Render to adjust. **Changing Streamlit sliders does not change worker settings.**
- Only one entry per ticker (enforced by SQLite unique constraint), including after restarts.
- Trades, market decisions, events and balance persist in `/data/sentinel.sqlite3` on Render's persistent disk.
- Expired positions are settled **only if** Kalshi's market API reports `status=settled` or `finalized` and `result=yes` or `no`. Otherwise they remain open/unresolved. This avoids inventing results, but can delay or miss settlement when the provider uses other statuses.
- The optional "Conservative Probability Edge" feature is NOT implemented in the worker. There is no calibrated probability model; the v7 checkbox actually prevents all entries when enabled.
- Health endpoint `/health` indicates server liveness, **not** feed freshness. Check the dashboard's `last_tick` and `last_error`.
- A single Render instance is required; don't increase web workers or scale to multiple instances because each would start another trading loop. The SQLite file must remain on its persistent disk.
- Estimated fees are simplified approximations, not a verified current Kalshi fee schedule. Paper fills assume best-price available depth, and do not model latency, partial fills, market impact or exchange downtime. Observed win rate may differ from live trading.

## Troubleshooting

- **No trades:** inspect worker `last_error` and interval `reason` column; filters intentionally skip intervals. The 15-minute BTC market may have no usable quotes.
- **No worker panel:** ensure both Streamlit secrets exist and restart app. Do not put the secret in code.
- **No settled outcomes:** the worker only settles on explicit Kalshi final results; unresolved positions are not counted as wins.
- **Worker inactive:** check Render logs, billing, and whether the service is running; Streamlit's refreshing does not keep the worker alive.

## Local syntax check

`python -m py_compile app.py strategy.py worker.py server.py`

For local testing, set `SENTINEL_DB=./sentinel.sqlite3` and `DASHBOARD_TOKEN` before running `uvicorn server:app --workers 1`. Keep local database out of Git.
