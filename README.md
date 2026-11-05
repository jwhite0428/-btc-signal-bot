# BTC Signal Bot
A simple live BTC chart that reports UP, DOWN, or WAIT from four indicators: EMA 9/21/50 alignment, RSI momentum, MACD, and candle direction. Optional volume confirmation. Evaluates the last fully closed candle and refreshes automatically.

Run with Python 3.10+:
```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
Coinbase/Kraken commonly use BTC/USD; Binance commonly uses BTC/USDT. Public data only. No exchange account connection or orders. "Locked" means indicators currently agree, not that future price direction is guaranteed.
