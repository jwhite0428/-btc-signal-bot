import streamlit as st
import ccxt, pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="BTC Signal Bot", page_icon="₿", layout="wide")
st.title("₿ BTC/USD Signal Bot")
st.caption("UP / DOWN / WAIT alerts from closed-candle indicators. No trades are placed.")

with st.sidebar:
    exchange_id=st.selectbox("Exchange",["coinbase","kraken","binance"])
    default_symbol="BTC/USDT" if exchange_id=="binance" else "BTC/USD"
    symbol=st.text_input("Symbol",default_symbol)
    tf=st.selectbox("Timeframe",["1m","5m","15m","30m","1h"],index=2)
    refresh=st.slider("Refresh seconds",5,60,15,5)
    required=st.slider("Indicators required to agree",2,4,3)
    volume_filter=st.checkbox("Require volume above average",False)

@st.cache_data(ttl=8)
def candles(exid,sym,timeframe):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":15000})
    raw=ex.fetch_ohlcv(sym,timeframe=timeframe,limit=150)
    return pd.DataFrame(raw,columns=["ms","open","high","low","close","volume"])

@st.fragment(run_every=refresh)
def dashboard():
    try:
        df=candles(exchange_id,symbol,tf).copy()
    except Exception as e:
        st.error(f"Market data error: {e}")
        st.info("Try BTC/USD on Coinbase or Kraken; BTC/USDT on Binance.")
        return
    df["time"]=pd.to_datetime(df.ms,unit="ms",utc=True)
    df["ema9"]=df.close.ewm(span=9,adjust=False).mean()
    df["ema21"]=df.close.ewm(span=21,adjust=False).mean()
    df["ema50"]=df.close.ewm(span=50,adjust=False).mean()
    d=df.close.diff()
    gain=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=gain/loss.replace(0,float("nan"))
    df["rsi"]=100-100/(1+rs)
    macd=df.close.ewm(span=12,adjust=False).mean()-df.close.ewm(span=26,adjust=False).mean()
    df["macd"]=macd
    df["macd_sig"]=macd.ewm(span=9,adjust=False).mean()
    df["vol_avg"]=df.volume.rolling(20).mean()

    # Evaluate the last fully closed candle, not the still-forming candle.
    r=df.iloc[-2]
    checks={
      "EMA alignment": 1 if r.ema9>r.ema21>r.ema50 else (-1 if r.ema9<r.ema21<r.ema50 else 0),
      "RSI momentum": 1 if 50<r.rsi<70 else (-1 if 30<r.rsi<50 else 0),
      "MACD": 1 if r.macd>r.macd_sig else (-1 if r.macd<r.macd_sig else 0),
      "Candle": 1 if r.close>r.open else (-1 if r.close<r.open else 0)
    }
    ups=sum(v==1 for v in checks.values())
    downs=sum(v==-1 for v in checks.values())
    vol_ok=pd.notna(r.vol_avg) and r.volume>r.vol_avg
    if volume_filter and not vol_ok: signal="WAIT"
    elif ups>=required and ups>downs: signal="UP"
    elif downs>=required and downs>ups: signal="DOWN"
    else: signal="WAIT"

    price=float(df.iloc[-1].close)
    a,b,c=st.columns(3)
    a.metric("BTC price",f"${price:,.2f}")
    b.metric("Signal",signal)
    c.metric("RSI",f"{r.rsi:.1f}" if pd.notna(r.rsi) else "—")
    if signal=="UP":
        st.success("🔒 UP SIGNAL LOCKED — current closed-candle indicators favor upward momentum.")
    elif signal=="DOWN":
        st.error("🔒 DOWN SIGNAL LOCKED — current closed-candle indicators favor downward momentum.")
    else:
        st.warning("WAIT — not enough indicators agree. No direction is locked.")
    st.caption(f"Evaluated candle: {r.time.strftime('%Y-%m-%d %H:%M UTC')} · Auto-refresh: {refresh}s")

    left,right=st.columns([2,1])
    with left:
        fig=go.Figure([go.Candlestick(x=df.time,open=df.open,high=df.high,low=df.low,close=df.close,name="BTC")])
        for col in ["ema9","ema21","ema50"]:
            fig.add_trace(go.Scatter(x=df.time,y=df[col],name=col.upper(),line=dict(width=1)))
        fig.update_layout(height=480,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=20,b=5))
        st.plotly_chart(fig,use_container_width=True)
    with right:
        st.subheader("Indicator confirmations")
        for name,val in checks.items():
            st.write(f"**{name}:** {'UP' if val==1 else 'DOWN' if val==-1 else 'NEUTRAL'}")
        st.write(f"**UP:** {ups}/4")
        st.write(f"**DOWN:** {downs}/4")
        st.write(f"**Volume confirmation:** {'YES' if vol_ok else 'NO'}")

    st.caption("A locked signal is only the current indicator state, not a guarantee of future price movement. Signals can reverse. This app reads public data only and never places orders.")

dashboard()
