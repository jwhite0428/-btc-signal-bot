# SENTINEL BTC 15-minute paper dashboard

A Streamlit dashboard designed to closely resemble the supplied dark Sentinel reference image: green locked call headline, 6 quote/market tiles, BTC candlestick chart with EMA and zones, DMI/ADX panel, indicator evidence and live evidence columns, and paper trade controls.

## Install on Streamlit Cloud

Upload `app.py` and `requirements.txt` to the **root** of your existing GitHub repository. Set Streamlit Main file path to `app.py`. Streamlit automatically installs dependencies. No API keys or real-money trading are used.

## Notes

- Live market discovery: Kalshi public `KXBTC15M` open series; quote data: public Kalshi order book.
- BTC technicals: public Coinbase BTC/USD candles; entries and exits are **Kalshi contract** bid/ask prices, not BTC spot.
- 13 indicators: EMA trend, DMI, ADX, SuperTrend, RSI, MACD, Bollinger, Ichimoku, volume, candle momentum, ATR breakout, Fibonacci position, short-term structure.
- Manual and automatic paper modes, paper take profit and stop loss, optional opposing-signal exit.
- No guaranteed alerts or trades when Streamlit sleeps; no background worker.
- Paper positions are session state and may disappear on app reboot. Expired contracts are not automatically settled, and fees/fills are approximations.
- The screenshot is a visual reference, not a verified model or profitability claim.
