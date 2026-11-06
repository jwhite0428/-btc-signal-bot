import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go

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
hr{border-color:#292f3b}
</style>
""", unsafe_allow_html=True)

st.title("₿ BTC SENTINEL")
st.caption("Live BTC market intelligence · 13-indicator engine · Reference trade planner · No live orders")

with st.sidebar:
    st.header("Market setup")
    exchange_name = st.selectbox("Exchange", ["Coinbase", "Kraken", "Binance"], index=0)
    symbols = {"Coinbase":["BTC/USD"], "Kraken":["BTC/USD"], "Binance":["BTC/USDT"]}
    symbol = st.selectbox("Pair", symbols[exchange_name])
    timeframe = st.selectbox("Chart timeframe", ["1m","5m","15m"], index=2)
    candle_limit = st.slider("Chart history", 150, 500, 300, 25)
    st.divider()
    st.subheader("Trade plan")
    entry_mode = st.radio("Buy-in reference", ["Current BTC price", "Set my own entry"], index=0)
    manual_entry = st.number_input("Your planned buy-in ($)", min_value=0.0, value=0.0, step=50.0, disabled=entry_mode=="Current BTC price")
    take_profit_pct = st.number_input("Take profit (%)", min_value=0.1, max_value=25.0, value=1.0, step=0.1)
    stop_loss_pct = st.number_input("Stop loss (%)", min_value=0.1, max_value=20.0, value=0.6, step=0.1)
    opposing_stop = st.checkbox("Exit plan if opposite signal confirms", value=True)
    st.caption("Buy-in and exits are planning references only. The app never places trades.")

EXCHANGE_CLASSES = {"Coinbase":ccxt.coinbase, "Kraken":ccxt.kraken, "Binance":ccxt.binance}

@st.cache_data(ttl=15, show_spinner=False)
def fetch_ohlcv(exchange_name, symbol, tf, limit):
    ex=EXCHANGE_CLASSES[exchange_name]({"enableRateLimit":True,"timeout":20000})
    rows=ex.fetch_ohlcv(symbol,timeframe=tf,limit=limit)
    df=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume"])
    df["time"]=pd.to_datetime(df.ts,unit="ms",utc=True)
    return df

def calc_indicators(df):
    x=df.copy()
    # 1-3: EMA 12/25/50
    for n in (12,25,50):
        x[f"ema{n}"]=x.close.ewm(span=n,adjust=False).mean()
    # 4: RSI
    delta=x.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x["rsi"]=100-100/(1+gain/loss.replace(0,np.nan))
    # 5: MACD
    x["macd"]=x.close.ewm(span=12,adjust=False).mean()-x.close.ewm(span=26,adjust=False).mean()
    x["macd_sig"]=x.macd.ewm(span=9,adjust=False).mean()
    # 6: Bollinger
    mid=x.close.rolling(20).mean(); sd=x.close.rolling(20).std()
    x["bb_mid"],x["bb_up"],x["bb_low"]=mid,mid+2*sd,mid-2*sd
    # 7: ATR
    tr=pd.concat([x.high-x.low,(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    x["atr"]=tr.ewm(alpha=1/14,adjust=False).mean()
    # 8-10: DMI +DI/-DI and ADX
    up=x.high.diff(); down=-x.low.diff()
    pdm=up.where((up>down)&(up>0),0.0); mdm=down.where((down>up)&(down>0),0.0)
    pdi=100*pdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan)
    mdi=100*mdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan)
    x["pdi"],x["mdi"]=pdi,mdi
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    x["adx"]=dx.ewm(alpha=1/14,adjust=False).mean()
    # 11: Ichimoku conversion/base relationship
    tenkan=(x.high.rolling(9).max()+x.low.rolling(9).min())/2
    kijun=(x.high.rolling(26).max()+x.low.rolling(26).min())/2
    x["ichi_bull"]=(tenkan>kijun)&(x.close>kijun)
    # 12: SuperTrend approximation
    hl2=(x.high+x.low)/2
    x["st_upper"]=hl2+3*x.atr
    x["st_lower"]=hl2-3*x.atr
    x["supertrend_bull"]=x.close>x.st_upper.shift(1)
    x["supertrend_bear"]=x.close<x.st_lower.shift(1)
    # 13: volume confirmation
    x["vol_avg"]=x.volume.rolling(20).mean()
    # Fibonacci retracement levels over recent 100 candles
    x["fib_high"]=x.high.rolling(100).max()
    x["fib_low"]=x.low.rolling(100).min()
    return x

def vote(x):
    r=x.iloc[-1]; p=x.iloc[-2]
    votes=[]
    def push(name,side,detail):
        votes.append({"Indicator":name,"Vote":side,"Reading":detail})
    push("EMA 12 / 25 / 50","UP" if r.ema12>r.ema25>r.ema50 else "DOWN" if r.ema12<r.ema25<r.ema50 else "NEUTRAL",f"{r.ema12:,.2f} / {r.ema25:,.2f} / {r.ema50:,.2f}")
    push("RSI (14)","UP" if 52<r.rsi<70 else "DOWN" if 30<r.rsi<48 else "NEUTRAL",f"{r.rsi:.1f}")
    push("MACD","UP" if r.macd>r.macd_sig else "DOWN" if r.macd<r.macd_sig else "NEUTRAL",f"{r.macd-r.macd_sig:+.3f}")
    push("Bollinger position","UP" if r.close>r.bb_mid else "DOWN" if r.close<r.bb_mid else "NEUTRAL",f"Lower {r.bb_low:,.2f} · Mid {r.bb_mid:,.2f} · Upper {r.bb_up:,.2f}")
    push("DMI +DI / -DI","UP" if r.pdi>r.mdi else "DOWN" if r.mdi>r.pdi else "NEUTRAL",f"{r.pdi:.1f} / {r.mdi:.1f}")
    push("ADX strength","UP" if r.adx>=20 and r.pdi>r.mdi else "DOWN" if r.adx>=20 and r.mdi>r.pdi else "NEUTRAL",f"{r.adx:.1f}")
    push("Ichimoku","UP" if bool(r.ichi_bull) else "DOWN" if r.close<((r.high+r.low)/2) else "NEUTRAL","Tenkan / Kijun / price check")
    push("SuperTrend","UP" if bool(r.supertrend_bull) else "DOWN" if bool(r.supertrend_bear) else "NEUTRAL",f"ATR {r.atr:,.2f}")
    push("Volume confirmation","UP" if r.close>r.open and r.volume>r.vol_avg else "DOWN" if r.close<r.open and r.volume>r.vol_avg else "NEUTRAL",f"{r.volume:,.3f} vs avg {r.vol_avg:,.3f}")
    push("Candle momentum","UP" if r.close>r.open and r.close>p.close else "DOWN" if r.close<r.open and r.close<p.close else "NEUTRAL",f"O {r.open:,.2f} · C {r.close:,.2f}")
    # Fibonacci location: vote only on meaningful close location
    fib_range=max(float(r.fib_high-r.fib_low),1e-9)
    fib_pos=(r.close-r.fib_low)/fib_range
    push("Fibonacci range","UP" if fib_pos<0.40 and r.close>p.close else "DOWN" if fib_pos>0.60 and r.close<p.close else "NEUTRAL",f"Range position {fib_pos*100:.1f}%")
    push("ATR volatility","UP" if r.close>p.close and r.atr>0 else "DOWN" if r.close<p.close and r.atr>0 else "NEUTRAL",f"ATR {r.atr:,.2f}")
    push("Short-term structure","UP" if r.close>r.ema12 and r.close>p.close else "DOWN" if r.close<r.ema12 and r.close<p.close else "NEUTRAL",f"Close vs EMA12")
    up=sum(v["Vote"]=="UP" for v in votes); dn=sum(v["Vote"]=="DOWN" for v in votes)
    sig="UP" if up>=8 and up-dn>=4 else "DOWN" if dn>=8 and dn-up>=4 else "WAIT"
    return sig,up,dn,votes

try:
    with st.spinner("Connecting to public exchange feed..."):
        raw=fetch_ohlcv(exchange_name,symbol,timeframe,candle_limit)
        data=calc_indicators(raw)
    if len(data)<110: st.error("Not enough candle history to calculate the full indicator set."); st.stop()
except Exception as e:
    st.error(f"Live market feed unavailable: {e}")
    st.info("Try another supported exchange/pair or refresh later.")
    st.stop()

r=data.iloc[-1]; prev=data.iloc[-2]
signal,up_votes,down_votes,votes=vote(data)
price=float(r.close)
entry=price if entry_mode=="Current BTC price" or manual_entry<=0 else float(manual_entry)
tp=entry*(1+take_profit_pct/100)
sl=entry*(1-stop_loss_pct/100)
signal_color="#35dc78" if signal=="UP" else "#ff5c70" if signal=="DOWN" else "#f2c45c"
evidence=round(max(up_votes,down_votes)/13*100)

# Main status header
st.markdown(f"<h2 style='color:{signal_color};margin-bottom:0'>{signal} SIGNAL · {'BUY BIAS' if signal=='UP' else 'SELL BIAS' if signal=='DOWN' else 'WAIT FOR CONFIRMATION'}</h2>",unsafe_allow_html=True)
st.markdown(f"<div class='muted'>{exchange_name} {symbol} · {timeframe} · {r.time.strftime('%Y-%m-%d %H:%M:%S UTC')} · {up_votes}/13 UP votes · {down_votes}/13 DOWN votes · evidence score is not a win probability</div>",unsafe_allow_html=True)
st.info("REFERENCE ONLY — no exchange order is submitted. Confirm the live quote and spread before entering any trade.")

# Top quote tiles
q=st.columns(6)
q[0].metric("BTC spot",f"${price:,.2f}",f"{price-float(prev.close):+,.2f}")
q[1].metric("BUY-IN reference",f"${entry:,.2f}")
q[2].metric("TAKE PROFIT",f"${tp:,.2f}",f"+{take_profit_pct:.1f}%")
q[3].metric("STOP LOSS",f"${sl:,.2f}",f"-{stop_loss_pct:.1f}%")
q[4].metric("UP / DOWN votes",f"{up_votes} / {down_votes}","of 13")
q[5].metric("Market regime","TRENDING" if r.adx>=20 else "RANGING",f"ADX {r.adx:.1f}")

st.subheader("Sentinel live chart")
show_zones=st.checkbox("Show buy-in / take-profit / stop zones",value=True)
show_labels=st.checkbox("Show EMA labels",value=True)
fig=go.Figure()
fig.add_trace(go.Candlestick(x=data.time,open=data.open,high=data.high,low=data.low,close=data.close,name="BTC"))
if show_labels:
    for col,label in [("ema12","EMA 12"),("ema25","EMA 25"),("ema50","EMA 50")]:
        fig.add_trace(go.Scatter(x=data.time,y=data[col],mode="lines",name=label,line=dict(width=1.3)))
if show_zones:
    fig.add_hline(y=entry,line_dash="dash",line_color="#f5c451",annotation_text="BUY-IN")
    fig.add_hline(y=tp,line_dash="dash",line_color="#35dc78",annotation_text="TAKE PROFIT")
    fig.add_hline(y=sl,line_dash="dash",line_color="#ff5c70",annotation_text="STOP")
fig.update_layout(template="plotly_dark",height=510,margin=dict(l=8,r=8,t=15,b=8),xaxis_rangeslider_visible=False,
                  paper_bgcolor="#090b10",plot_bgcolor="#090b10",legend=dict(orientation="h",y=1.02))
st.plotly_chart(fig,use_container_width=True)

# DMI/ADX secondary pane
dmi=go.Figure()
dmi.add_trace(go.Scatter(x=data.time,y=data.pdi,name="+DI",line=dict(color="#35dc78")))
dmi.add_trace(go.Scatter(x=data.time,y=data.mdi,name="-DI",line=dict(color="#ff5c70")))
dmi.add_trace(go.Scatter(x=data.time,y=data.adx,name="ADX",line=dict(color="#b38cff")))
dmi.update_layout(template="plotly_dark",height=210,margin=dict(l=8,r=8,t=10,b=8),paper_bgcolor="#090b10",plot_bgcolor="#090b10")
st.plotly_chart(dmi,use_container_width=True)

left,right=st.columns([1,1])
with left:
    st.subheader("13-indicator evidence")
    table=pd.DataFrame(votes)
    def color_vote(val):
        if val=="UP": return "color:#35dc78;font-weight:bold"
        if val=="DOWN": return "color:#ff5c70;font-weight:bold"
        return "color:#aab3c2"
    st.dataframe(table.style.map(color_vote,subset=["Vote"]),hide_index=True,use_container_width=True,height=520)
with right:
    st.subheader("Live evidence breakdown")
    st.markdown(f"<div class='panel'><div class='muted'>DIRECTIONAL CONFLUENCE — NOT PROBABILITY</div><div style='font-size:36px;font-weight:800;color:{signal_color}'>{evidence}%</div><div class='muted'>Most-supported side: {signal if signal!='WAIT' else 'No clear side'}</div><hr>",unsafe_allow_html=True)
    groups=[
        ("Trend indicators",["EMA 12 / 25 / 50","SuperTrend","Ichimoku","Short-term structure"],4),
        ("Momentum indicators",["RSI (14)","MACD","Candle momentum"],3),
        ("Strength / volatility",["DMI +DI / -DI","ADX strength","ATR volatility"],3),
        ("Price / participation",["Bollinger position","Volume confirmation","Fibonacci range"],3)]
    vote_map={v["Indicator"]:v["Vote"] for v in votes}
    for label,names,weight in groups:
        score=sum(vote_map[n]=="UP" for n in names) if signal=="UP" else sum(vote_map[n]=="DOWN" for n in names)
        pct=score/len(names)*100
        st.markdown(f"**{label}** · {weight}/13 indicators")
        st.progress(int(pct),text=f"{score}/{len(names)} aligned with {signal if signal!='WAIT' else 'leading side'}")
    st.markdown("</div>",unsafe_allow_html=True)
    st.subheader("Trade management")
    st.write(f"**Buy-in reference:** ${entry:,.2f}")
    st.write(f"**Take-profit target:** ${tp:,.2f} (+{take_profit_pct:.2f}%)")
    st.write(f"**Stop-loss reference:** ${sl:,.2f} (-{stop_loss_pct:.2f}%)")
    st.write(f"**Opposing-signal exit:** {'Enabled in your plan' if opposing_stop else 'Disabled in your plan'}")
    st.caption("These are planning levels calculated from your selected entry, not automatic orders or alerts.")

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

st.caption("Indicators are calculated from completed exchange candles. The newest candle can still be forming. Refresh the page for updated data.")
st.caption("Crypto markets are volatile. This dashboard is educational, does not provide guaranteed signals, and does not execute or manage trades.")
