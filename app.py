import time
from datetime import datetime, timezone

import ccxt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="BTC/USD Strategy Lab", page_icon="₿", layout="wide")
st.title("₿ BTC/USD Strategy Lab")
st.caption("Live public market data • strategy signals • paper trading only")

# Safety: this starter project never submits exchange orders.
DEFAULTS = {
    "symbol": "BTC/USD",
    "timeframe": "1m",
    "limit": 300,
    "refresh_seconds": 15,
    "take_profit_pct": 1.0,
    "stop_loss_pct": 0.5,
    "trade_size_usd": 100.0,
    "max_trades": 1,
    "strategy": "Combined consensus",
    "min_votes": 2,
    "fee_pct": 0.10,
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value
if "paper_position" not in st.session_state:
    st.session_state.paper_position = None
if "paper_trades" not in st.session_state:
    st.session_state.paper_trades = []
if "starting_balance" not in st.session_state:
    st.session_state.starting_balance = 1000.0
if "realized_pnl" not in st.session_state:
    st.session_state.realized_pnl = 0.0
if "last_auto_entry_candle" not in st.session_state:
    st.session_state.last_auto_entry_candle = None

with st.sidebar:
    st.header("Settings")
    exchange_id = st.selectbox("Public data source", ["coinbase", "kraken", "binance"], index=0)
    symbol = st.text_input("Market symbol", value=st.session_state.symbol, help="Examples: BTC/USD for Coinbase or Kraken; BTC/USDT for Binance.")
    timeframe = st.selectbox("Candle interval", ["1m", "5m", "15m", "30m", "1h", "4h"], index=0)
    candle_limit = st.slider("Candles to load", 100, 500, 300, 50)
    refresh_seconds = st.slider("Auto-refresh (seconds)", 5, 60, 15, 5)
    st.divider()
    st.subheader("Strategy")
    strategy = st.selectbox(
        "Entry model",
        ["Combined consensus", "EMA crossover", "RSI reversal", "MACD momentum", "Breakout"],
    )
    min_votes = st.slider("Votes required (combined)", 1, 4, 2)
    st.divider()
    st.subheader("Paper risk controls")
    tp = st.number_input("Take profit (%)", min_value=0.1, max_value=50.0, value=1.0, step=0.1)
    sl = st.number_input("Stop loss (%)", min_value=0.1, max_value=50.0, value=0.5, step=0.1)
    size_usd = st.number_input("Paper position notional ($)", min_value=10.0, max_value=100000.0, value=100.0, step=10.0)
    fee_pct = st.number_input("Estimated fee per side (%)", min_value=0.0, max_value=2.0, value=0.10, step=0.01)
    auto_entry = st.checkbox("Auto-enter on UP/DOWN signal (paper only)", value=False)
    starting_balance = st.number_input("Paper starting balance ($)", min_value=100.0, max_value=1000000.0, value=float(st.session_state.starting_balance), step=100.0)
    st.session_state.starting_balance = starting_balance
    st.warning("Paper trading only. No exchange orders are placed.")

st.session_state.symbol = symbol
st.session_state.timeframe = timeframe
st.session_state.refresh_seconds = refresh_seconds
st.session_state.take_profit_pct = tp
st.session_state.stop_loss_pct = sl
st.session_state.trade_size_usd = size_usd
st.session_state.strategy = strategy
st.session_state.min_votes = min_votes
st.session_state.fee_pct = fee_pct

st_autorefresh(interval=refresh_seconds * 1000, key="btc_refresh")

@st.cache_data(ttl=8, show_spinner=False)
def fetch_candles(exchange_name, market_symbol, tf, limit):
    ex_class = getattr(ccxt, exchange_name)
    exchange = ex_class({"enableRateLimit": True, "timeout": 15000})
    candles = exchange.fetch_ohlcv(market_symbol, timeframe=tf, limit=limit)
    if not candles:
        raise RuntimeError("Exchange returned no candles.")
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def add_indicators(df):
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    df["rsi"] = 100 - (100 / (1 + rs))
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["prior_high20"] = df["high"].rolling(20).max().shift(1)
    df["prior_low20"] = df["low"].rolling(20).min().shift(1)
    return df

def evaluate_signals(df, required_votes):
    # Use the most recent completed candle when possible to reduce intrabar signal flicker.
    row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    prev = df.iloc[-3] if len(df) >= 3 else row
    votes = []
    details = []

    ema_up = row.ema9 > row.ema21 and prev.ema9 <= prev.ema21
    ema_down = row.ema9 < row.ema21 and prev.ema9 >= prev.ema21
    if ema_up:
        votes.append(1); details.append("EMA crossover: UP")
    elif ema_down:
        votes.append(-1); details.append("EMA crossover: DOWN")
    else:
        details.append("EMA crossover: no fresh cross")

    if pd.notna(row.rsi) and row.rsi < 30:
        votes.append(1); details.append(f"RSI reversal: UP (RSI {row.rsi:.1f})")
    elif pd.notna(row.rsi) and row.rsi > 70:
        votes.append(-1); details.append(f"RSI reversal: DOWN (RSI {row.rsi:.1f})")
    else:
        details.append(f"RSI: neutral ({row.rsi:.1f})" if pd.notna(row.rsi) else "RSI: warming up")

    if row.macd > row.macd_signal and prev.macd <= prev.macd_signal:
        votes.append(1); details.append("MACD cross: UP")
    elif row.macd < row.macd_signal and prev.macd >= prev.macd_signal:
        votes.append(-1); details.append("MACD cross: DOWN")
    else:
        details.append("MACD: no fresh cross")

    if pd.notna(row.prior_high20) and row.close > row.prior_high20:
        votes.append(1); details.append("20-candle breakout: UP")
    elif pd.notna(row.prior_low20) and row.close < row.prior_low20:
        votes.append(-1); details.append("20-candle breakdown: DOWN")
    else:
        details.append("Breakout: none")

    up_votes, down_votes = votes.count(1), votes.count(-1)
    if up_votes >= required_votes and up_votes > down_votes:
        combined = "UP"
    elif down_votes >= required_votes and down_votes > up_votes:
        combined = "DOWN"
    else:
        combined = "NO TRADE"

    if st.session_state.strategy == "EMA crossover":
        chosen = "UP" if ema_up else ("DOWN" if ema_down else "NO TRADE")
    elif st.session_state.strategy == "RSI reversal":
        chosen = "UP" if pd.notna(row.rsi) and row.rsi < 30 else ("DOWN" if pd.notna(row.rsi) and row.rsi > 70 else "NO TRADE")
    elif st.session_state.strategy == "MACD momentum":
        chosen = "UP" if row.macd > row.macd_signal and prev.macd <= prev.macd_signal else ("DOWN" if row.macd < row.macd_signal and prev.macd >= prev.macd_signal else "NO TRADE")
    elif st.session_state.strategy == "Breakout":
        chosen = "UP" if pd.notna(row.prior_high20) and row.close > row.prior_high20 else ("DOWN" if pd.notna(row.prior_low20) and row.close < row.prior_low20 else "NO TRADE")
    else:
        chosen = combined

    return chosen, details, up_votes, down_votes, row

def close_paper_position(exit_price, reason):
    pos = st.session_state.paper_position
    if not pos:
        return
    direction_sign = 1 if pos["side"] == "LONG" else -1
    gross = (exit_price - pos["entry"]) * pos["qty"] * direction_sign
    fees = (pos["entry"] * pos["qty"] + exit_price * pos["qty"]) * (st.session_state.fee_pct / 100.0)
    net = gross - fees
    trade = {
        "entry_time": pos["entry_time"], "exit_time": datetime.now(timezone.utc).isoformat(),
        "side": pos["side"], "entry": pos["entry"], "exit": exit_price,
        "qty": pos["qty"], "gross_pnl": gross, "fees": fees, "net_pnl": net, "reason": reason
    }
    st.session_state.paper_trades.insert(0, trade)
    st.session_state.realized_pnl += net
    st.session_state.paper_position = None

def manage_paper_position(price):
    pos = st.session_state.paper_position
    if not pos:
        return
    if pos["side"] == "LONG":
        tp_price = pos["entry"] * (1 + st.session_state.take_profit_pct / 100)
        sl_price = pos["entry"] * (1 - st.session_state.stop_loss_pct / 100)
        if price >= tp_price:
            close_paper_position(price, "Take profit")
        elif price <= sl_price:
            close_paper_position(price, "Stop loss")
    else:
        tp_price = pos["entry"] * (1 - st.session_state.take_profit_pct / 100)
        sl_price = pos["entry"] * (1 + st.session_state.stop_loss_pct / 100)
        if price <= tp_price:
            close_paper_position(price, "Take profit")
        elif price >= sl_price:
            close_paper_position(price, "Stop loss")

try:
    with st.spinner("Loading public BTC/USD candles…"):
        raw = fetch_candles(exchange_id, symbol, timeframe, candle_limit)
    df = add_indicators(raw)
    current_price = float(df.iloc[-1]["close"])
    signal, details, up_votes, down_votes, signal_row = evaluate_signals(df, min_votes)
except Exception as exc:
    st.error(f"Could not load candles for {exchange_id} / {symbol}: {exc}")
    st.info("Check the symbol format for the selected exchange, then refresh.")
    st.stop()

# Simulated position management checks each refresh against latest candle close.
manage_paper_position(current_price)

# Optional signal-driven paper entry. Never places real exchange orders.
if auto_entry and st.session_state.paper_position is None and signal in ("UP", "DOWN"):
    candle_key = str(signal_row["timestamp"])
    if candle_key != st.session_state.last_auto_entry_candle:
        qty = st.session_state.trade_size_usd / current_price
        st.session_state.paper_position = {
            "side": "LONG" if signal == "UP" else "SHORT",
            "entry": current_price,
            "qty": qty,
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }
        st.session_state.last_auto_entry_candle = candle_key

last_time = df.iloc[-1]["timestamp"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest BTC price", f"${current_price:,.2f}")
c2.metric("Selected strategy", signal)
c3.metric("RSI (14)", f"{float(signal_row['rsi']):.1f}" if pd.notna(signal_row["rsi"]) else "—")
c4.metric("Last candle timestamp (UTC)", last_time.strftime("%H:%M:%S"))

fig = go.Figure(data=[go.Candlestick(
    x=df["timestamp"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="BTC/USD"
)])
fig.add_trace(go.Scatter(x=df["timestamp"], y=df["ema9"], name="EMA 9", line=dict(width=1.2)))
fig.add_trace(go.Scatter(x=df["timestamp"], y=df["ema21"], name="EMA 21", line=dict(width=1.2)))
fig.add_trace(go.Scatter(x=df["timestamp"], y=df["ema50"], name="EMA 50", line=dict(width=1.2)))
fig.update_layout(height=560, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=25, b=10), legend=dict(orientation="h"))
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns([1, 1])
with left:
    st.subheader("Strategy diagnostics")
    st.write(f"**Combined votes:** UP {up_votes} · DOWN {down_votes}")
    for item in details:
        st.write("• " + item)
    st.caption("Signals use historical candles and are not forecasts or guarantees. Current candle may still be forming.")
with right:
    st.subheader("Paper trading controls")
    if st.session_state.paper_position is None:
        a, b = st.columns(2)
        with a:
            if st.button("Open paper LONG", type="primary", use_container_width=True):
                qty = st.session_state.trade_size_usd / current_price
                st.session_state.paper_position = {
                    "side": "LONG", "entry": current_price, "qty": qty,
                    "entry_time": datetime.now(timezone.utc).isoformat()
                }
                st.rerun()
        with b:
            if st.button("Open paper SHORT", use_container_width=True):
                qty = st.session_state.trade_size_usd / current_price
                st.session_state.paper_position = {
                    "side": "SHORT", "entry": current_price, "qty": qty,
                    "entry_time": datetime.now(timezone.utc).isoformat()
                }
                st.rerun()
        st.caption("Manual paper entry is available by default. Enable Auto-enter in the sidebar to let the selected signal open paper positions.")
    else:
        pos = st.session_state.paper_position
        sign = 1 if pos["side"] == "LONG" else -1
        unrealized = (current_price - pos["entry"]) * pos["qty"] * sign
        tp_price = pos["entry"] * (1 + st.session_state.take_profit_pct/100 * sign)
        sl_price = pos["entry"] * (1 - st.session_state.stop_loss_pct/100 * sign)
        st.write(f"**Open {pos['side']}** · Entry ${pos['entry']:,.2f} · Size ${pos['entry']*pos['qty']:,.2f}")
        st.write(f"Take-profit trigger: ${tp_price:,.2f}")
        st.write(f"Stop-loss trigger: ${sl_price:,.2f}")
        st.metric("Unrealized P&L before fees", f"${unrealized:,.2f}")
        if st.button("Close paper position now", use_container_width=True):
            close_paper_position(current_price, "Manual close")
            st.rerun()

st.divider()
st.subheader("Paper account")
balance = st.session_state.starting_balance + st.session_state.realized_pnl
m1, m2, m3, m4 = st.columns(4)
m1.metric("Starting balance", f"${st.session_state.starting_balance:,.2f}")
m2.metric("Realized net P&L", f"${st.session_state.realized_pnl:,.2f}")
m3.metric("Balance incl. realized P&L", f"${balance:,.2f}")
m4.metric("Closed paper trades", str(len(st.session_state.paper_trades)))

if st.session_state.paper_trades:
    trades_df = pd.DataFrame(st.session_state.paper_trades)
    st.dataframe(trades_df, use_container_width=True, hide_index=True)
    csv = trades_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download paper trade log (CSV)", csv, "btc_paper_trades.csv", "text/csv")
else:
    st.info("No paper trades closed yet.")

with st.expander("Important limitations and next steps"):
    st.markdown("""
- This app fetches public candles and supports manual paper entries. It does **not** send live orders.
- The paper position and trade log live in Streamlit session memory and may reset when the app restarts.
- Take-profit/stop-loss checks occur on refresh using the latest candle close, so they can miss intrabar touches and cannot guarantee an execution price.
- The estimated fee is a simplified per-side percentage; real fees, spread, slippage, funding, and tax are not fully modeled.
- Before live execution, add persistent storage, exchange-specific order/fill reconciliation, minimum order checks, API key handling via environment variables, max daily loss, kill switch, and extensive paper testing.
- Do not put API secrets into this file or commit them to source control.
""")

# --- Kalshi 15-minute contract paper-lock module (public quotes, no orders) ---
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

st.divider()
st.header("Kalshi contract lock · paper trading")
st.caption("Public Kalshi market quotes; simulated fills at displayed ask/bid. No orders or API keys. Evidence scores are NOT win probabilities. Uses the currently selected BTC candle interval; choose 1m for short-term entries.")

for k, v in {
    "k_position": None, "k_trades": [], "k_realized": 0.0,
    "k_used_markets": [], "k_last_event": "Scanning", "k_entry_evidence": 70,
    "k_opposition": 20, "k_tp_cents": 20,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

@st.cache_data(ttl=5, show_spinner=False)
def kalshi_public_market(ticker):
    """Read publicly accessible market and orderbook; never place an order."""
    root = "https://api.elections.kalshi.com/trade-api/v2"
    def get(path):
        req = Request(root + path, headers={"Accept": "application/json", "User-Agent": "BTC-Paper-Monitor/1.0"})
        with urlopen(req, timeout=8) as response:
            return json.load(response)
    from urllib.parse import quote
    safe_ticker = quote(ticker.strip().upper(), safe="-")
    market = get("/markets/" + safe_ticker).get("market", {})
    book = get("/markets/" + safe_ticker + "/orderbook").get("orderbook", {})
    return market, book

@st.cache_data(ttl=10, show_spinner=False)
def kalshi_discover_15m():
    """Find the nearest currently open BTC 15-minute contract from public Kalshi API."""
    from urllib.parse import urlencode
    root = "https://api.elections.kalshi.com/trade-api/v2"
    candidates = []
    cursor = None
    now = datetime.now(timezone.utc)
    # Small bounded pagination; do not synthesize tickers from clock time.
    for _ in range(4):
        params = {"series_ticker": "KXBTC15M", "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        req = Request(root + "/markets?" + urlencode(params), headers={"Accept": "application/json", "User-Agent": "BTC-Paper-Monitor/1.0"})
        with urlopen(req, timeout=8) as response:
            payload = json.load(response)
        for market in payload.get("markets", []):
            ticker = str(market.get("ticker", "")).upper()
            if not ticker.startswith("KXBTC15M-"):
                continue
            if str(market.get("status", "")).lower() not in ("active", "open"):
                continue
            close_raw = market.get("close_time") or market.get("expiration_time")
            if not close_raw:
                continue
            try:
                close = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if close <= now:
                continue
            open_raw = market.get("open_time")
            if open_raw:
                try:
                    if datetime.fromisoformat(open_raw.replace("Z", "+00:00")) > now:
                        continue
                except ValueError:
                    continue
            candidates.append((close, ticker))
        cursor = payload.get("cursor")
        if not cursor:
            break
    if not candidates:
        raise ValueError("No currently open KXBTC15M market found in the Kalshi API response")
    return min(candidates)[1]

def cents_value(v):
    """Kalshi *_dollars fields are dollar prices, older fields are integer cents."""
    try:
        if v is None or v == "":
            return None
        return round(float(v), 4)
    except (ValueError, TypeError):
        return None

def market_field_cents(m, side, direction):
    prefix = side.lower() + "_" + direction.lower()
    d = cents_value(m.get(prefix + "_dollars"))
    if d is not None:
        return round(d * 100, 3)
    c = cents_value(m.get(prefix))
    return c

def best_bid(levels):
    """Levels can be [price_cents, quantity] or [price_dollars, quantity] in *_dollars fields."""
    vals = []
    for item in levels or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            p = cents_value(item[0])
            if p is not None and float(item[1]) > 0:
                vals.append(p)
    return max(vals) if vals else None

def contract_quotes(m, book):
    quotes = {}
    for side in ("yes", "no"):
        quotes[side] = {kind: market_field_cents(m, side, kind) for kind in ("bid", "ask")}
    # Kalshi orderbook holds YES and NO bids; opposite bid implies the ask.
    for side in ("yes", "no"):
        if quotes[side]["bid"] is None:
            raw = book.get(side + "_dollars")
            val = best_bid(raw)
            if val is not None:
                quotes[side]["bid"] = round(val * 100, 3)
            else:
                quotes[side]["bid"] = best_bid(book.get(side))
    for side, other in (("yes", "no"), ("no", "yes")):
        if quotes[side]["ask"] is None and quotes[other]["bid"] is not None:
            quotes[side]["ask"] = round(100 - quotes[other]["bid"], 3)
    for side in quotes:
        for kind in ("ask", "bid"):
            p = quotes[side][kind]
            if p is not None and not (0 < p < 100):
                quotes[side][kind] = None
    return quotes

def evidence_score(candles):
    """Transparent 5-factor directional agreement on last completed candle."""
    x = candles.iloc[-2]
    prev = candles.iloc[-3]
    up = down = 0.0
    factors = []
    def vote(name, direction, weight):
        nonlocal up, down
        if direction == "UP": up += weight
        elif direction == "DOWN": down += weight
        factors.append({"Indicator": name, "Vote": direction, "Weight": f"{weight:.0f}%"})
    vote("EMA 9 vs EMA 21", "UP" if x.ema9 > x.ema21 else "DOWN", 25)
    vote("Price vs EMA 50", "UP" if x.close > x.ema50 else "DOWN", 20)
    vote("MACD histogram", "UP" if x.macd_hist > 0 else "DOWN", 20)
    if pd.isna(x.rsi):
        rsi_vote = "NEUTRAL"
    else:
        rsi_vote = "UP" if x.rsi >= 53 else ("DOWN" if x.rsi <= 47 else "NEUTRAL")
    vote("RSI momentum", rsi_vote, 20)
    vote("Closed-candle momentum", "UP" if x.close > prev.close else ("DOWN" if x.close < prev.close else "NEUTRAL"), 15)
    return round(up, 1), round(down, 1), factors, x.timestamp

with st.expander("Kalshi lock settings", expanded=True):
    k_auto_switch = st.checkbox("Automatically find and switch BTC 15-minute markets", value=True, help="Uses the public Kalshi KXBTC15M market listing. An existing paper lock stays attached to its original market until exited or cleared.")
    k_ticker = st.text_input("Manual Kalshi market ticker (used when auto-switch is off)", value="", placeholder="KXBTC15M-...", disabled=k_auto_switch)
    k_auto = st.checkbox("Automatically lock a paper entry when evidence reaches threshold", value=False)
    k_entry = st.slider("Entry evidence threshold (%)", 50, 100, int(st.session_state.k_entry_evidence), 5)
    k_opp = st.slider("Opposing evidence increase needed for exit (percentage points)", 5, 80, int(st.session_state.k_opposition), 5)
    k_tp = st.slider("Take profit above fill (cents per contract)", 1, 50, int(st.session_state.k_tp_cents), 1)
    k_contracts = st.number_input("Paper contracts per entry", 1, 1000, 1, 1)
    k_fee = st.number_input("Estimated fee (cents per contract per side)", 0.0, 10.0, 1.0, 0.25, help="User-adjustable approximation, not a Kalshi fee quote. Real fees can vary by price, market and order type.")
    k_buffer = st.slider("Do not open new trades in final N seconds", 0, 180, 30, 5)
    st.session_state.k_entry_evidence = k_entry
    st.session_state.k_opposition = k_opp
    st.session_state.k_tp_cents = k_tp
    st.caption("Opposing exits require a rise from entry of the chosen percentage points, at least 60% opposing evidence, and confirmation on 2 different completed candles. Take-profit exits do not wait for confirmation.")

k_up, k_down, k_factors, k_candle = evidence_score(df)
a, b, c = st.columns(3)
a.metric("UP evidence", f"{k_up:.0f}%")
b.metric("DOWN evidence", f"{k_down:.0f}%")
c.metric("Entry threshold", f"{k_entry}%")
st.dataframe(pd.DataFrame(k_factors), hide_index=True, use_container_width=True)

k_market = k_book = k_quotes = None
k_error = None
k_seconds = None
k_market_ticker = k_ticker.strip().upper()
if k_auto_switch:
    if st.session_state.k_position:
        # Never abandon an open paper position merely because a new interval began.
        k_market_ticker = st.session_state.k_position["ticker"]
        st.info(f"Tracking locked market {k_market_ticker} until exit; automatic interval switching is paused.")
    else:
        try:
            k_market_ticker = kalshi_discover_15m()
            st.success(f"Auto-selected current Kalshi interval: {k_market_ticker}")
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            k_market_ticker = ""
            st.warning(f"Automatic market discovery unavailable: {e}. No paper entries will be made.")
if k_market_ticker:
    try:
        k_market, k_book = kalshi_public_market(k_market_ticker)
        if k_market.get("ticker", "").upper() != k_market_ticker:
            raise ValueError("Market ticker mismatch")
        k_quotes = contract_quotes(k_market, k_book)
        close_str = k_market.get("close_time") or k_market.get("expiration_time")
        if close_str:
            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            k_seconds = (close_dt - datetime.now(timezone.utc)).total_seconds()
        q1, q2, q3, q4 = st.columns(4)
        def price_text(x):
            return "—" if x is None else f"{x:.1f}¢"
        q1.metric("YES ask", price_text(k_quotes["yes"]["ask"]))
        q2.metric("YES bid", price_text(k_quotes["yes"]["bid"]))
        q3.metric("NO ask", price_text(k_quotes["no"]["ask"]))
        q4.metric("NO bid", price_text(k_quotes["no"]["bid"]))
        st.caption(f"Market: {k_market_ticker} · Status: {k_market.get('status', 'unknown')} · Time to close: {int(k_seconds) if k_seconds is not None else 'unknown'} seconds. Quotes are snapshots and may change before an order could fill.")
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        k_error = str(e)
        st.warning(f"Kalshi quotes unavailable: {k_error}. Entries and quote-based exits are disabled until quotes recover.")
else:
    st.info("No valid Kalshi market is selected. Check automatic discovery or enter a manual ticker. No fabricated contract prices are used.")

k_pos = st.session_state.k_position
k_status = str(k_market.get("status", "")).lower() if k_market else ""
k_open = k_market is not None and k_status in ("active", "open") and k_seconds is not None and k_seconds > k_buffer
k_ready = k_quotes is not None and k_error is None

# Close only at the currently observed executable bid; never pretend an ask is a sell fill.
if k_pos:
    if k_market_ticker != k_pos["ticker"]:
        st.warning("An existing paper lock belongs to another ticker. Re-enter that ticker to resume exit monitoring.")
    elif k_ready and k_seconds is not None and k_seconds > 0 and k_status in ("active", "open"):
        side = k_pos["side"]
        bid = k_quotes[side]["bid"]
        opposition = k_down if side == "yes" else k_up
        tp_trigger = k_pos["entry_ask"] + k_pos["tp_cents"]
        exit_reason = None
        if bid is not None and bid >= tp_trigger:
            exit_reason = "Take profit"
        else:
            # Compare with the opposing evidence AT ENTRY, not an absolute 20% score.
            # Only count a new completed candle once; two consecutive qualifying
            # closed candles are required to avoid instant exits on refresh.
            opposing_trigger = max(60.0, k_pos.get("entry_opposition", 100 - k_pos["evidence"]) + k_pos["opposition_threshold"])
            if k_pos.get("last_checked_candle") != str(k_candle):
                k_pos["last_checked_candle"] = str(k_candle)
                if opposition >= opposing_trigger:
                    k_pos["opposing_confirmations"] = k_pos.get("opposing_confirmations", 0) + 1
                else:
                    k_pos["opposing_confirmations"] = 0
            if bid is not None and k_pos.get("opposing_confirmations", 0) >= 2 and opposition >= opposing_trigger:
                exit_reason = "Confirmed opposing evidence"
        if exit_reason:
            gross_cents = (bid - k_pos["entry_ask"]) * k_pos["contracts"]
            fees_cents = k_pos["fee_cents"] * 2 * k_pos["contracts"]
            pnl_dollars = (gross_cents - fees_cents) / 100
            st.session_state.k_realized += pnl_dollars
            st.session_state.k_trades.insert(0, {
                "ticker": k_pos["ticker"], "side": side.upper(), "entry_time": k_pos["time"],
                "exit_time": datetime.now(timezone.utc).isoformat(), "entry_ask_cents": k_pos["entry_ask"],
                "exit_bid_cents": bid, "contracts": k_pos["contracts"], "entry_evidence_pct": k_pos["evidence"],
                "gross_pnl_usd": round(gross_cents / 100, 4), "estimated_fees_usd": round(fees_cents / 100, 4),
                "net_pnl_usd": round(pnl_dollars, 4), "exit_reason": exit_reason,
            })
            st.session_state.k_last_event = f"Exited {side.upper()} at {bid:.1f}¢ · {exit_reason}"
            st.session_state.k_position = None
            k_pos = None

if not k_pos and k_auto and k_open and k_ready and k_market_ticker not in st.session_state.k_used_markets:
    selected = "yes" if k_up >= k_entry and k_up > k_down else ("no" if k_down >= k_entry and k_down > k_up else None)
    if selected and k_quotes[selected]["ask"] is not None and k_quotes[selected]["bid"] is not None:
        ask = k_quotes[selected]["ask"]
        # Reject crossed/invalid quote snapshots and targets above the maximum payout.
        if k_quotes[selected]["bid"] <= ask and ask + k_tp < 100:
            st.session_state.k_position = {
                "ticker": k_market_ticker, "side": selected, "entry_ask": ask,
                "contracts": int(k_contracts), "evidence": k_up if selected == "yes" else k_down,
                "time": datetime.now(timezone.utc).isoformat(), "tp_cents": k_tp,
                "opposition_threshold": k_opp, "entry_opposition": k_down if selected == "yes" else k_up,
                "last_checked_candle": str(k_candle), "opposing_confirmations": 0,
                "fee_cents": float(k_fee),
            }
            st.session_state.k_used_markets.append(k_market_ticker)
            st.session_state.k_last_event = f"Locked {selected.upper()} paper entry at {ask:.1f}¢"
            k_pos = st.session_state.k_position

if k_pos:
    st.success(f"LOCKED {k_pos['side'].upper()} · {k_pos['ticker']} · Paper fill {k_pos['entry_ask']:.1f}¢ · TP {k_pos['entry_ask'] + k_pos['tp_cents']:.1f}¢")
    if k_ready and k_market_ticker == k_pos["ticker"]:
        bid = k_quotes[k_pos["side"]]["bid"]
        if bid is not None:
            gross = (bid - k_pos["entry_ask"]) * k_pos["contracts"] / 100
            st.metric("Mark-to-bid paper P&L (before fees)", f"${gross:+.2f}")
        if k_seconds is not None and k_seconds <= 0:
            st.warning("Market has closed. This app does not infer settlement or payout; await verified resolution. No new entries are allowed.")
    if k_seconds is not None and k_seconds <= 0:
        st.warning("Expired lock remains attached to this interval. Confirm settlement externally before clearing; this app will not fabricate a payout.")
        if st.button("Clear expired paper lock (no P&L recorded)"):
            st.session_state.k_last_event = f"Cleared expired lock {k_pos['ticker']} without settlement accounting"
            st.session_state.k_position = None
            st.rerun()
    if st.button("Manually exit paper lock at current bid", disabled=not(k_ready and k_market_ticker == k_pos["ticker"] and k_seconds is not None and k_seconds > 0 and k_status in ("active", "open") and k_quotes[k_pos["side"]]["bid"] is not None)):
        bid = k_quotes[k_pos["side"]]["bid"]
        gross_cents = (bid - k_pos["entry_ask"]) * k_pos["contracts"]
        fees_cents = k_pos["fee_cents"] * 2 * k_pos["contracts"]
        net = (gross_cents - fees_cents) / 100
        st.session_state.k_realized += net
        st.session_state.k_trades.insert(0, {"ticker": k_pos["ticker"], "side": k_pos["side"].upper(), "entry_time": k_pos["time"], "exit_time": datetime.now(timezone.utc).isoformat(), "entry_ask_cents": k_pos["entry_ask"], "exit_bid_cents": bid, "contracts": k_pos["contracts"], "entry_evidence_pct": k_pos["evidence"], "gross_pnl_usd": round(gross_cents / 100, 4), "estimated_fees_usd": round(fees_cents / 100, 4), "net_pnl_usd": round(net, 4), "exit_reason": "Manual exit"})
        st.session_state.k_last_event = f"Manual exit at {bid:.1f}¢"
        st.session_state.k_position = None
        st.rerun()
else:
    st.info("SCANNING · No Kalshi paper position locked")
st.caption("Latest event: " + st.session_state.k_last_event)
st.metric("Kalshi paper realized net P&L (estimated)", f"${st.session_state.k_realized:+.2f}")
if st.session_state.k_trades:
    log = pd.DataFrame(st.session_state.k_trades)
    st.dataframe(log, hide_index=True, use_container_width=True)
    st.download_button("Download Kalshi paper trades CSV", log.to_csv(index=False).encode(), "kalshi_paper_trades.csv", "text/csv")
st.caption("Limitations: Session state and trade history may reset when Streamlit restarts or the phone session ends. Quotes are snapshots, not guaranteed fills; thin books, slippage, fees, latency, contract resolution and market halts can change outcomes. Automatic monitoring requires the Streamlit session to keep refreshing. This module never places real orders.")
