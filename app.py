import math
from datetime import datetime, timezone
import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Kalshi BTC Paper Desk", page_icon="₿", layout="wide")
st.markdown('''<style>
.stApp{background:#0b1018;color:#edf3fb}section[data-testid="stSidebar"]{background:#121a27}
.block-container{max-width:1200px;padding-top:1rem}div[data-testid="stMetric"]{background:#151f2e;border:1px solid #2a3a4e;padding:12px;border-radius:12px}
div.stButton>button{border-radius:10px;font-weight:650;min-height:2.8rem}
</style>''', unsafe_allow_html=True)
st.title("₿ BTC 15-MIN PAPER DESK")
st.caption("Real Kalshi public quotes · simulated YES/NO trades · no real money or account connection")
BASE="https://api.elections.kalshi.com/trade-api/v2"
SERIES="KXBTC15M"

for key, default in {"balance":1000.0,"positions":[],"closed":[],"events":[],"next_id":1,"last_market":""}.items():
    if key not in st.session_state: st.session_state[key]=default

@st.cache_data(ttl=8,show_spinner=False)
def api(path, params=None):
    r=requests.get(BASE+path,params=params,timeout=12,headers={"Accept":"application/json","User-Agent":"BTC-Paper-Desk/1.0"})
    r.raise_for_status()
    return r.json()

def price(raw):
    try:
        v=float(raw)
        return v if 0<=v<=1 else None
    except (ValueError,TypeError): return None

def cents(raw):
    try:
        v=float(raw)/100
        return v if 0<=v<=1 else None
    except (ValueError,TypeError): return None

def get_px(m, side, kind):
    # Kalshi commonly exposes fixed-point dollars fields; use cents only as fallback.
    stem=f"{side.lower()}_{kind.lower()}"
    for key in (stem+"_dollars", stem+"_fp"):
        if key in m and m[key] not in (None,""):
            v=price(m[key]);
            if v is not None:return v
    return cents(m.get(stem))

def quote_from_book(b):
    ob=b.get("orderbook",{})
    def best(side):
        levels=ob.get(side+"_dollars")
        if levels is not None:
            vals=[price(level[0]) for level in levels if isinstance(level,(list,tuple)) and len(level)>1]
        else:
            vals=[cents(level[0]) for level in ob.get(side,[]) if isinstance(level,(list,tuple)) and len(level)>1]
        return max((v for v in vals if v is not None),default=None)
    yb,nb=best("yes"),best("no")
    return {"YES":{"bid":yb,"ask":round(1-nb,4) if nb is not None else None},"NO":{"bid":nb,"ask":round(1-yb,4) if yb is not None else None}}

def fmt(p):return "—" if p is None else f"{p*100:.1f}¢"
def cashfmt(x):return f"${x:,.2f}"
def fee(qty,px):
    # Approximate taker fee; not an exact or guaranteed Kalshi fee schedule.
    return math.ceil(0.07*qty*px*(1-px)*100)/100

def expiry(m):
    for k in ("close_time","expiration_time","expected_expiration_time"):
        if m.get(k):
            try:return pd.Timestamp(m[k])
            except (ValueError,TypeError):pass
    return None

def record(kind,msg):
    st.session_state.events.insert(0,{"UTC":datetime.now(timezone.utc).strftime("%H:%M:%S"),"Type":kind,"Message":msg})
    st.session_state.events=st.session_state.events[:50]

with st.sidebar:
    st.header("Simple controls")
    refresh=st.toggle("Live updates",value=True)
    interval=st.select_slider("Refresh every",options=[5,10,15,30],value=10)
    st.divider()
    st.metric("Paper cash",cashfmt(st.session_state.balance))
    if st.button("Reset $1,000 paper account",use_container_width=True):
        st.session_state.balance=1000.0
        st.session_state.positions=[]
        st.session_state.closed=[]
        st.session_state.events=[]
        st.session_state.next_id=1
        st.rerun()
    st.caption("Balances and trades are saved in this browser session only.")

if refresh: st_autorefresh(interval=interval*1000,key="market_refresh")

# Discover via known BTC 15-minute series rather than scanning unrelated events.
try:
    response=api("/markets",{"series_ticker":SERIES,"status":"open","limit":100})
    markets=response.get("markets",[])
except Exception as exc:
    markets=[]
    st.warning(f"Market discovery temporarily unavailable: {exc}")

st.subheader("1 · Choose a 15-minute contract")
manual=st.toggle("Enter a Kalshi ticker manually",value=not bool(markets))
if manual:
    ticker=st.text_input("Contract ticker",placeholder="Paste the exact Kalshi BTC 15m ticker").strip().upper()
    if not ticker:st.info("Paste the exact 15-minute BTC contract ticker from Kalshi to continue.");st.stop()
else:
    markets.sort(key=lambda m:str(m.get("close_time") or m.get("expiration_time") or ""))
    labels=[f"{m.get('ticker','')}  ·  {m.get('title') or 'BTC 15-minute'}" for m in markets]
    chosen=st.selectbox("Live BTC 15-minute market",range(len(labels)),format_func=lambda i:labels[i])
    ticker=markets[chosen]["ticker"]

try:
    market=api("/markets/"+ticker).get("market",{})
    if not market:raise ValueError("No market returned")
    if not ticker.startswith(SERIES):
        st.error("This does not appear to be a BTC 15-minute contract (KXBTC15M). No trades allowed.");st.stop()
    book=api("/markets/"+ticker+"/orderbook")
    quotes=quote_from_book(book)
except Exception as exc:
    st.error(f"Could not load the selected Kalshi contract: {exc}");st.stop()

if st.session_state.last_market and st.session_state.last_market!=ticker:
    st.info("You switched markets. Existing paper positions remain attached to their original tickers.")
st.session_state.last_market=ticker
end=expiry(market)
remaining=max(0,int((end-pd.Timestamp.now(tz="UTC")).total_seconds())) if end is not None else None
open_now=market.get("status")=="active" or market.get("status")=="open"
if remaining is not None and remaining==0:open_now=False

st.caption(f"{market.get('title','Bitcoin 15-minute market')} · `{ticker}`")
cols=st.columns(5)
cols[0].metric("YES buy / sell",f"{fmt(quotes['YES']['ask'])} / {fmt(quotes['YES']['bid'])}")
cols[1].metric("NO buy / sell",f"{fmt(quotes['NO']['ask'])} / {fmt(quotes['NO']['bid'])}")
cols[2].metric("Time left",f"{remaining//60:02d}:{remaining%60:02d}" if remaining is not None else "Unknown")
cols[3].metric("Market status",str(market.get("status","unknown")).upper())
cols[4].metric("Paper cash",cashfmt(st.session_state.balance))

st.subheader("2 · Paper buy")
left,right=st.columns([1,1])
with left:
    side=st.segmented_control("Pick a direction",["YES","NO"],default="YES")
    qty=st.number_input("Number of contracts",min_value=1,max_value=10000,value=5,step=1)
with right:
    ask=quotes[side]["ask"]
    st.metric("Live buy-in (ask)",fmt(ask))
    take_profit=st.slider("Take profit: gain per contract (cents)",1,50,10)
    stop_loss=st.slider("Stop loss: drop per contract (cents)",1,50,10)

estimated_cost=(qty*ask+fee(qty,ask)) if ask is not None else None
st.write(f"**Estimated paper cost:** {cashfmt(estimated_cost) if estimated_cost is not None else 'No ask available'} (includes estimated fee)")
if st.button(f"Buy {qty} {side} paper contracts",type="primary",use_container_width=True,disabled=not open_now or ask is None):
    if ask is None or ask<=0 or ask>=1:st.error("A valid live ask is required.")
    else:
        cost=round(qty*ask+fee(qty,ask),2)
        if cost>st.session_state.balance:st.error("Not enough paper cash.")
        else:
            st.session_state.balance=round(st.session_state.balance-cost,2)
            position={"id":st.session_state.next_id,"ticker":ticker,"side":side,"qty":int(qty),"entry":ask,"cost":cost,"tp":min(.99,round(ask+take_profit/100,4)),"sl":max(.01,round(ask-stop_loss/100,4)),"opened":datetime.now(timezone.utc).isoformat()}
            st.session_state.positions.append(position)
            st.session_state.next_id+=1
            record("BUY",f"{qty} {side} {ticker} @ {fmt(ask)}")
            st.rerun()

st.caption("A paper buy fills at the displayed ask. Real orders can slip or remain unfilled. The entry and take-profit are contract prices, not BTC/USD price targets.")

st.subheader("3 · Open paper trades")
if not st.session_state.positions:st.info("No open paper trades yet. Choose YES or NO and tap Buy.")

# Fetch quotes for positions on other tickers as needed.
quote_cache={ticker:quotes}
for p in list(st.session_state.positions):
    pt=p["ticker"]
    if pt not in quote_cache:
        try:quote_cache[pt]=quote_from_book(api("/markets/"+pt+"/orderbook"))
        except Exception:quote_cache[pt]={"YES":{"bid":None,"ask":None},"NO":{"bid":None,"ask":None}}
    bid=quote_cache[pt][p["side"]]["bid"]
    pnl=(p["qty"]*bid-p["cost"]-fee(p["qty"],bid)) if bid is not None else None
    c1,c2,c3,c4=st.columns([2,1,1,1])
    c1.write(f"**{p['side']} × {p['qty']}** · `{pt}`")
    c1.caption(f"Entry {fmt(p['entry'])} · TP {fmt(p['tp'])} · SL {fmt(p['sl'])}")
    c2.metric("Sell now (bid)",fmt(bid))
    c3.metric("Estimated P/L",cashfmt(pnl) if pnl is not None else "—")
    reached_tp=bid is not None and bid>=p["tp"]
    reached_sl=bid is not None and bid<=p["sl"]
    if reached_tp:st.success(f"Take-profit level reached on paper position #{p['id']}. Tap Sell to close.")
    if reached_sl:st.warning(f"Stop-loss level reached on paper position #{p['id']}. Tap Sell to close.")
    if c4.button("Sell paper",key=f"sell_{p['id']}",disabled=bid is None):
        proceeds=round(p["qty"]*bid-fee(p["qty"],bid),2)
        realized=round(proceeds-p["cost"],2)
        st.session_state.balance=round(st.session_state.balance+proceeds,2)
        st.session_state.positions.remove(p)
        st.session_state.closed.insert(0,{"ticker":pt,"side":p["side"],"qty":p["qty"],"buy":fmt(p["entry"]),"sell":fmt(bid),"P/L ($)":realized})
        record("SELL",f"{p['qty']} {p['side']} {pt} @ {fmt(bid)} · P/L {cashfmt(realized)}")
        st.rerun()

st.subheader("Account overview")
marked_value=0.0
unknown=False
for p in st.session_state.positions:
    bid=quote_cache.get(p["ticker"],{}).get(p["side"],{}).get("bid")
    if bid is None:unknown=True
    else:marked_value+=p["qty"]*bid
c1,c2,c3=st.columns(3)
c1.metric("Cash",cashfmt(st.session_state.balance))
c2.metric("Open positions",str(len(st.session_state.positions)))
c3.metric("Estimated account value",cashfmt(st.session_state.balance+marked_value) if not unknown else "Unavailable")

with st.expander("BTC reference chart (optional)"):
    @st.cache_data(ttl=15,show_spinner=False)
    def btc_chart():
        data=requests.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",params={"granularity":60},timeout=12)
        data.raise_for_status()
        df=pd.DataFrame(data.json(),columns=["ts","low","high","open","close","volume"])
        df["time"]=pd.to_datetime(df.ts,unit="s",utc=True)
        return df.sort_values("time")
    try:
        d=btc_chart()
        fig=go.Figure(go.Candlestick(x=d.time,open=d.open,high=d.high,low=d.low,close=d.close,name="BTC/USD"))
        fig.update_layout(template="plotly_dark",height=330,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=10,b=5),paper_bgcolor="#0b1018",plot_bgcolor="#0b1018")
        st.plotly_chart(fig,use_container_width=True)
    except Exception as exc:st.info(f"Reference BTC chart unavailable: {exc}")

if st.session_state.closed:
    with st.expander("Closed paper trades"):
        st.dataframe(pd.DataFrame(st.session_state.closed),hide_index=True,use_container_width=True)
if st.session_state.events:
    with st.expander("Paper activity log"):
        st.dataframe(pd.DataFrame(st.session_state.events),hide_index=True,use_container_width=True)

st.caption("Simulation only: no real trades, deposits or account login. Refresh and paper positions require an active Streamlit browser session. TP/SL are visual triggers, not automated orders. No official settlement payout is credited automatically; you can paper-sell at a live bid before settlement. Exact fees, execution queue, liquidity and partial fills are not modeled.")
