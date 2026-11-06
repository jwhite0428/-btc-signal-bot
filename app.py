import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="BTC Sentinel Desk", page_icon="₿", layout="wide")
st.markdown("""
<style>
.stApp{background:#090b10;color:#e9edf5}
section[data-testid="stSidebar"]{background:#11141c}
.block-container{max-width:1500px;padding-top:1rem;padding-bottom:2rem}
div[data-testid="stMetric"]{background:#151922;border:1px solid #292f3b;border-radius:10px;padding:12px}
.panel{background:#131720;border:1px solid #292f3b;border-radius:12px;padding:15px}
.muted{color:#8f9aaa;font-size:12px}
.green{color:#35dc78}.red{color:#ff5c70}.amber{color:#f2c45c}
div.stButton>button{border-radius:9px}
</style>
""", unsafe_allow_html=True)

st.title("₿ BTC SENTINEL")
st.caption("Live BTC market intelligence · 13 indicators · Locked signal + reversal watch · No live orders")

if "locked_signal" not in st.session_state:
    st.session_state.locked_signal = None
if "opposite_count" not in st.session_state:
    st.session_state.opposite_count = 0
if "last_alert_key" not in st.session_state:
    st.session_state.last_alert_key = None
if "alert_history" not in st.session_state:
    st.session_state.alert_history = []
if "lock_started" not in st.session_state:
    st.session_state.lock_started = None

with st.sidebar:
    st.header("Market setup")
    exchange_name = st.selectbox("Exchange", ["Coinbase", "Kraken", "Binance"], index=0)
    symbol = st.selectbox("Pair", ["BTC/USD"] if exchange_name != "Binance" else ["BTC/USDT"])
    timeframe = st.selectbox("Chart timeframe", ["1m","5m","15m"], index=2)
    candle_limit = st.slider("Chart history", 150, 500, 300, 25)
    st.divider()
    st.subheader("Live updates")
    auto_refresh = st.toggle("Auto-refresh dashboard", value=True)
    refresh_seconds = st.select_slider("Refresh interval", options=[5,10,15,30,60], value=15)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=refresh_seconds*1000, key="btc_auto_refresh")
    elif auto_refresh:
        st.warning("Auto-refresh package unavailable. Use the refresh button.")
    if st.button("↻ Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.subheader("Signal lock")
    lock_enabled = st.toggle("Lock first confirmed direction", value=True)
    min_votes = st.slider("Minimum directional votes", 6, 12, 8)
    vote_gap = st.slider("Minimum vote advantage", 1, 8, 4)
    reversal_confirmations = st.slider("Opposite confirmations to unlock", 1, 5, 2)
    if st.button("Reset signal lock", use_container_width=True):
        st.session_state.locked_signal = None
        st.session_state.opposite_count = 0
        st.session_state.lock_started = None
        st.session_state.last_alert_key = None
        st.session_state.alert_history = []
        st.rerun()
    st.divider()
    st.subheader("Trade plan")
    entry_mode = st.radio("Buy-in reference", ["Current BTC price", "Set my own entry"])
    manual_entry = st.number_input("Your planned buy-in ($)", min_value=0.0, value=0.0, step=50.0, disabled=entry_mode=="Current BTC price")
    take_profit_pct = st.number_input("Take profit (%)", min_value=0.1, max_value=25.0, value=1.0, step=0.1)
    stop_loss_pct = st.number_input("Stop loss (%)", min_value=0.1, max_value=20.0, value=0.6, step=0.1)
    st.caption("All levels are references. No exchange orders are submitted.")

EXCHANGE_CLASSES = {"Coinbase":ccxt.coinbase, "Kraken":ccxt.kraken, "Binance":ccxt.binance}

@st.cache_data(ttl=10, show_spinner=False)
def fetch_ohlcv(exchange_name, symbol, tf, limit):
    ex=EXCHANGE_CLASSES[exchange_name]({"enableRateLimit":True,"timeout":20000})
    rows=ex.fetch_ohlcv(symbol,timeframe=tf,limit=limit)
    df=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume"])
    df["time"]=pd.to_datetime(df.ts,unit="ms",utc=True)
    return df

def calc_indicators(df):
    x=df.copy()
    for n in (12,25,50):
        x[f"ema{n}"]=x.close.ewm(span=n,adjust=False).mean()
    delta=x.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x["rsi"]=100-100/(1+gain/loss.replace(0,np.nan))
    x["macd"]=x.close.ewm(span=12,adjust=False).mean()-x.close.ewm(span=26,adjust=False).mean()
    x["macd_sig"]=x.macd.ewm(span=9,adjust=False).mean()
    mid=x.close.rolling(20).mean(); sd=x.close.rolling(20).std()
    x["bb_mid"],x["bb_up"],x["bb_low"]=mid,mid+2*sd,mid-2*sd
    tr=pd.concat([x.high-x.low,(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    x["atr"]=tr.ewm(alpha=1/14,adjust=False).mean()
    up=x.high.diff(); down=-x.low.diff()
    pdm=up.where((up>down)&(up>0),0.0); mdm=down.where((down>up)&(down>0),0.0)
    pdi=100*pdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan)
    mdi=100*mdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan)
    x["pdi"],x["mdi"]=pdi,mdi
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    x["adx"]=dx.ewm(alpha=1/14,adjust=False).mean()
    tenkan=(x.high.rolling(9).max()+x.low.rolling(9).min())/2
    kijun=(x.high.rolling(26).max()+x.low.rolling(26).min())/2
    x["ichi_bull"]=(tenkan>kijun)&(x.close>kijun)
    hl2=(x.high+x.low)/2
    x["st_upper"]=hl2+3*x.atr
    x["st_lower"]=hl2-3*x.atr
    x["st_bull"]=x.close>x.st_upper.shift(1)
    x["st_bear"]=x.close<x.st_lower.shift(1)
    x["vol_avg"]=x.volume.rolling(20).mean()
    x["fib_high"]=x.high.rolling(100).max()
    x["fib_low"]=x.low.rolling(100).min()
    return x

def evaluate(x):
    r,p=x.iloc[-1],x.iloc[-2]
    votes=[]
    def add(name,side,reading):
        votes.append({"Indicator":name,"Vote":side,"Reading":reading})
    add("EMA 12 / 25 / 50","UP" if r.ema12>r.ema25>r.ema50 else "DOWN" if r.ema12<r.ema25<r.ema50 else "NEUTRAL",f"{r.ema12:,.2f} / {r.ema25:,.2f} / {r.ema50:,.2f}")
    add("RSI (14)","UP" if 52<r.rsi<70 else "DOWN" if 30<r.rsi<48 else "NEUTRAL",f"{r.rsi:.1f}")
    add("MACD","UP" if r.macd>r.macd_sig else "DOWN" if r.macd<r.macd_sig else "NEUTRAL",f"{r.macd-r.macd_sig:+.3f}")
    add("Bollinger position","UP" if r.close>r.bb_mid else "DOWN" if r.close<r.bb_mid else "NEUTRAL",f"{r.bb_low:,.2f} / {r.bb_mid:,.2f} / {r.bb_up:,.2f}")
    add("DMI +DI / -DI","UP" if r.pdi>r.mdi else "DOWN" if r.mdi>r.pdi else "NEUTRAL",f"{r.pdi:.1f} / {r.mdi:.1f}")
    add("ADX strength","UP" if r.adx>=20 and r.pdi>r.mdi else "DOWN" if r.adx>=20 and r.mdi>r.pdi else "NEUTRAL",f"{r.adx:.1f}")
    add("Ichimoku","UP" if bool(r.ichi_bull) else "DOWN" if r.close<((r.high+r.low)/2) else "NEUTRAL","Tenkan / Kijun / price")
    add("SuperTrend","UP" if bool(r.st_bull) else "DOWN" if bool(r.st_bear) else "NEUTRAL",f"ATR {r.atr:,.2f}")
    add("Volume confirmation","UP" if r.close>r.open and r.volume>r.vol_avg else "DOWN" if r.close<r.open and r.volume>r.vol_avg else "NEUTRAL",f"{r.volume:,.3f} vs avg {r.vol_avg:,.3f}")
    add("Candle momentum","UP" if r.close>r.open and r.close>p.close else "DOWN" if r.close<r.open and r.close<p.close else "NEUTRAL",f"O {r.open:,.2f} / C {r.close:,.2f}")
    span=max(float(r.fib_high-r.fib_low),1e-9); fibpos=(r.close-r.fib_low)/span
    add("Fibonacci range","UP" if fibpos<.40 and r.close>p.close else "DOWN" if fibpos>.60 and r.close<p.close else "NEUTRAL",f"{fibpos*100:.1f}% of 100-candle range")
    add("ATR volatility","UP" if r.close>p.close else "DOWN" if r.close<p.close else "NEUTRAL",f"{r.atr:,.2f}")
    add("Short-term structure","UP" if r.close>r.ema12 and r.close>p.close else "DOWN" if r.close<r.ema12 and r.close<p.close else "NEUTRAL","Close vs EMA12")
    ups=sum(v["Vote"]=="UP" for v in votes); downs=sum(v["Vote"]=="DOWN" for v in votes)
    raw="UP" if ups>=min_votes and ups-downs>=vote_gap else "DOWN" if downs>=min_votes and downs-ups>=vote_gap else "WAIT"
    return raw,ups,downs,votes

try:
    with st.spinner("Updating BTC market feed..."):
        raw=fetch_ohlcv(exchange_name,symbol,timeframe,candle_limit)
        data=calc_indicators(raw)
    if len(data)<110:
        st.error("Not enough candle history returned for all indicators."); st.stop()
except Exception as exc:
    st.error(f"Live feed unavailable: {exc}")
    st.info("Try another supported market or refresh later."); st.stop()

r=data.iloc[-1]; prior=data.iloc[-2]
raw_signal,up_votes,down_votes,votes=evaluate(data)
price=float(r.close)
bar_key=str(r.ts)
now=datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

# Lock engine: lock the first strong direction; require N consecutive opposite refreshes to flip.
alert_message=None
if lock_enabled:
    if st.session_state.locked_signal is None:
        if raw_signal in ("UP","DOWN"):
            st.session_state.locked_signal=raw_signal
            st.session_state.lock_started=now
            st.session_state.opposite_count=0
    elif raw_signal == st.session_state.locked_signal:
        st.session_state.opposite_count=0
    elif raw_signal in ("UP","DOWN") and raw_signal != st.session_state.locked_signal:
        st.session_state.opposite_count += 1
        if st.session_state.opposite_count >= reversal_confirmations:
            old=st.session_state.locked_signal
            new=raw_signal
            alert_message=f"REVERSAL CONFIRMED: {old} → {new} at ${price:,.2f}"
            st.session_state.locked_signal=new
            st.session_state.lock_started=now
            st.session_state.opposite_count=0
            alert_key=f"{bar_key}:{old}:{new}"
            if st.session_state.last_alert_key != alert_key:
                st.session_state.alert_history.insert(0,{"Time":now,"Event":alert_message})
                st.session_state.last_alert_key=alert_key
                st.session_state.alert_history=st.session_state.alert_history[:20]
    display_signal=st.session_state.locked_signal or "WAIT"
else:
    display_signal=raw_signal

if alert_message:
    st.error("🔔 "+alert_message)
    st.toast(alert_message,icon="🔔")
elif lock_enabled and st.session_state.locked_signal and raw_signal not in ("WAIT",st.session_state.locked_signal):
    st.warning(f"Reversal watch: {raw_signal} is opposing locked {st.session_state.locked_signal}. Confirmation {st.session_state.opposite_count}/{reversal_confirmations}.")

entry=price if entry_mode=="Current BTC price" or manual_entry<=0 else float(manual_entry)
tp=entry*(1+take_profit_pct/100)
sl=entry*(1-stop_loss_pct/100)
color="#35dc78" if display_signal=="UP" else "#ff5c70" if display_signal=="DOWN" else "#f2c45c"
evidence=round(max(up_votes,down_votes)/13*100)

st.markdown(f"<h2 style='color:{color};margin-bottom:0'>{display_signal} · {'CALL LOCKED' if display_signal=='UP' and lock_enabled else 'PUT LOCKED' if display_signal=='DOWN' and lock_enabled else 'WAIT / NO LOCK'}</h2>",unsafe_allow_html=True)
st.markdown(f"<div class='muted'>{exchange_name} {symbol} · {timeframe} · {r.time.strftime('%Y-%m-%d %H:%M:%S UTC')} · Raw signal: {raw_signal} · {up_votes} UP / {down_votes} DOWN votes</div>",unsafe_allow_html=True)
st.info("Signal lock and reversal watch operate while this dashboard session is open and refreshing. They do not submit trades.")

tiles=st.columns(6)
tiles[0].metric("BTC spot",f"${price:,.2f}",f"{price-float(prior.close):+,.2f}")
tiles[1].metric("Buy-in reference",f"${entry:,.2f}")
tiles[2].metric("Take profit",f"${tp:,.2f}",f"+{take_profit_pct:.1f}%")
tiles[3].metric("Stop loss",f"${sl:,.2f}",f"-{stop_loss_pct:.1f}%")
tiles[4].metric("Raw votes",f"{up_votes} / {down_votes}","UP / DOWN of 13")
tiles[5].metric("Market regime","TRENDING" if r.adx>=20 else "RANGING",f"ADX {r.adx:.1f}")

st.subheader("Sentinel live chart")
show_zones=st.checkbox("Show buy-in / target / stop levels",value=True)
fig=go.Figure()
fig.add_trace(go.Candlestick(x=data.time,open=data.open,high=data.high,low=data.low,close=data.close,name="BTC"))
for col,label in [("ema12","EMA 12"),("ema25","EMA 25"),("ema50","EMA 50")]:
    fig.add_trace(go.Scatter(x=data.time,y=data[col],mode="lines",name=label,line=dict(width=1.3)))
if show_zones:
    fig.add_hline(y=entry,line_dash="dash",line_color="#f2c45c",annotation_text="BUY-IN")
    fig.add_hline(y=tp,line_dash="dash",line_color="#35dc78",annotation_text="TAKE PROFIT")
    fig.add_hline(y=sl,line_dash="dash",line_color="#ff5c70",annotation_text="STOP")
fig.update_layout(template="plotly_dark",height=500,margin=dict(l=8,r=8,t=10,b=8),xaxis_rangeslider_visible=False,paper_bgcolor="#090b10",plot_bgcolor="#090b10",legend=dict(orientation="h",y=1.02))
st.plotly_chart(fig,use_container_width=True)

st.subheader("13-indicator evidence")
st.dataframe(pd.DataFrame(votes),hide_index=True,use_container_width=True)

st.subheader("Reversal alert history")
if st.session_state.alert_history:
    st.dataframe(pd.DataFrame(st.session_state.alert_history),hide_index=True,use_container_width=True)
else:
    st.caption("No confirmed reversals in this session yet.")

st.subheader("Momentum panels")
c1,c2=st.columns(2)
with c1:
    f=go.Figure(go.Scatter(x=data.time,y=data.rsi,name="RSI 14"))
    f.add_hline(y=70,line_dash="dash"); f.add_hline(y=30,line_dash="dash")
    f.update_layout(template="plotly_dark",height=220,margin=dict(l=5,r=5,t=5,b=5),paper_bgcolor="#090b10",plot_bgcolor="#090b10",yaxis_range=[0,100])
    st.plotly_chart(f,use_container_width=True)
with c2:
    f=go.Figure()
    f.add_trace(go.Bar(x=data.time,y=data.macd-data.macd_sig,name="MACD histogram"))
    f.add_trace(go.Scatter(x=data.time,y=data.macd,name="MACD"))
    f.add_trace(go.Scatter(x=data.time,y=data.macd_sig,name="Signal"))
    f.update_layout(template="plotly_dark",height=220,margin=dict(l=5,r=5,t=5,b=5),paper_bgcolor="#090b10",plot_bgcolor="#090b10")
    st.plotly_chart(f,use_container_width=True)

st.caption(f"Last candle timestamp: {r.time.strftime('%Y-%m-%d %H:%M UTC')} · Last dashboard refresh: {now}")
st.caption("Important: browser/session alerts are not guaranteed push notifications. Keep the app open and auto-refresh enabled. The app does not monitor in the background after the session closes.")
st.caption("Educational only. Scores are not probabilities. Take-profit and stop-loss are reference levels, not guaranteed fills. Crypto is high risk.")
