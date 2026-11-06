import streamlit as st
import requests, sqlite3, time, math
from datetime import datetime, timezone
from pathlib import Path

st.set_page_config(page_title='Kalshi BTC Paper Trader v5',page_icon='₿',layout='wide')
st.markdown('''<style>.stApp{background:#0b1019;color:#edf3fc}.block-container{max-width:1150px;padding-top:1.2rem}div[data-testid="stMetric"]{background:#172030;border:1px solid #2b3c50;padding:12px;border-radius:12px}section[data-testid="stSidebar"]{background:#111927}</style>''',unsafe_allow_html=True)
st.title('₿ Kalshi BTC 15m · Paper Trader')
st.caption('Automatic Kalshi market connection · Manual + Auto paper trades · NO real-money orders')
BASE='https://external-api.kalshi.com/trade-api/v2'
DB=Path(__file__).parent/'paper_trader.db'

def conn():
    c=sqlite3.connect(str(DB),timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, side TEXT, qty INTEGER, entry REAL, entry_fee REAL, entry_time TEXT, tp REAL, sl REAL, status TEXT DEFAULT "OPEN", exit REAL, exit_fee REAL, pnl REAL, exit_time TEXT, reason TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)')
    c.commit(); return c

def setting(k,default):
    with conn() as c:
        row=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone()
        return row['v'] if row else default

def save_setting(k,v):
    with conn() as c:c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)',(k,str(v)))

def get(path,params=None):
    r=requests.get(BASE+path,params=params,timeout=12,headers={'User-Agent':'PaperTraderDemo/5.0'})
    r.raise_for_status();return r.json()

@st.cache_data(ttl=8,show_spinner=False)
def markets():
    return get('/markets',{'series_ticker':'KXBTC15M','status':'open','limit':100}).get('markets',[])

@st.cache_data(ttl=5,show_spinner=False)
def market_detail(ticker):
    return get('/markets/'+ticker).get('market',{})

@st.cache_data(ttl=5,show_spinner=False)
def quotes(ticker):
    m=market_detail(ticker)
    def dec(k):
        v=m.get(k)
        try:return float(v) if v is not None and v!='' else None
        except:return None
    yb,ya,nb,na=map(dec,['yes_bid_dollars','yes_ask_dollars','no_bid_dollars','no_ask_dollars'])
    # Only use quotes actually returned by Kalshi; don't fabricate a tradable quote.
    if any(v is None for v in (yb,ya,nb,na)):
        try:
            ob=get('/markets/'+ticker+'/orderbook').get('orderbook_fp',{})
            yes=ob.get('yes_dollars',[]) or []; no=ob.get('no_dollars',[]) or []
            if yb is None and yes:yb=max(float(x[0]) for x in yes)
            if nb is None and no:nb=max(float(x[0]) for x in no)
            if ya is None and nb is not None:ya=1-nb
            if na is None and yb is not None:na=1-yb
        except requests.RequestException:pass
    return m,{'YES':{'bid':yb,'ask':ya},'NO':{'bid':nb,'ask':na}}

@st.cache_data(ttl=12,show_spinner=False)
def btc_signal():
    # Public Coinbase 1m candles, 100 candles, no credentials.
    data=requests.get('https://api.exchange.coinbase.com/products/BTC-USD/candles',params={'granularity':60},timeout=12).json()
    rows=sorted([x for x in data if isinstance(x,list) and len(x)>=5],key=lambda x:x[0])[-100:]
    if len(rows)<55:raise ValueError('Not enough BTC candles')
    closes=[float(x[4]) for x in rows]
    def ema(seq,n):
        alpha=2/(n+1); v=seq[0]
        for z in seq[1:]:v=alpha*z+(1-alpha)*v
        return v
    e9,e21,e50=(ema(closes,n) for n in (9,21,50))
    diffs=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    gain=sum(max(v,0) for v in diffs[-14:])/14
    loss=sum(max(-v,0) for v in diffs[-14:])/14
    rsi=100 if loss==0 else 100-100/(1+gain/loss)
    macd=ema(closes,12)-ema(closes,26)
    prev=ema(closes[:-1],12)-ema(closes[:-1],26)
    ups=int(e9>e21>e50)*2+int(closes[-1]>e21)+int(rsi>53)+int(macd>prev)
    downs=int(e9<e21<e50)*2+int(closes[-1]<e21)+int(rsi<47)+int(macd<prev)
    side='YES' if ups>=4 and ups-downs>=2 else 'NO' if downs>=4 and downs-ups>=2 else 'WAIT'
    return {'price':closes[-1],'side':side,'up':ups,'down':downs,'rsi':rsi}

def fee(price,qty):
    # Simplified estimate; not Kalshi's exact live fee schedule.
    return math.ceil(100*0.07*qty*price*(1-price))/100

def open_positions():
    with conn() as c:return [dict(x) for x in c.execute('SELECT * FROM positions WHERE status="OPEN" ORDER BY id DESC')]

def all_positions():
    with conn() as c:return [dict(x) for x in c.execute('SELECT * FROM positions ORDER BY id DESC LIMIT 100')]

def cash():
    with conn() as c:
        positions=c.execute('SELECT * FROM positions').fetchall()
    value=float(setting('starting_cash','1000'))
    for p in positions:
        value-=p['qty']*p['entry']+p['entry_fee']
        if p['status']=='CLOSED':value+=p['qty']*p['exit']-p['exit_fee']
    return round(value,2)

def buy(ticker,side,qty,ask,tp,sl):
    if ask is None or not (0<ask<1):return 'No executable ask available'
    cost=qty*ask+fee(ask,qty)
    if cash()+1e-9<cost:return 'Insufficient paper cash'
    with conn() as c:
        c.execute('INSERT INTO positions(ticker,side,qty,entry,entry_fee,entry_time,tp,sl,status) VALUES(?,?,?,?,?,?,?,?,"OPEN")',(ticker,side,qty,ask,fee(ask,qty),datetime.now(timezone.utc).isoformat(),tp,sl))
    return None

def sell(p,bid,reason):
    if bid is None or not (0<=bid<=1):return False
    xf=fee(bid,p['qty'])
    pnl=round(p['qty']*(bid-p['entry'])-p['entry_fee']-xf,2)
    with conn() as c:
        c.execute('UPDATE positions SET status="CLOSED",exit=?,exit_fee=?,pnl=?,exit_time=?,reason=? WHERE id=? AND status="OPEN"',(bid,xf,pnl,datetime.now(timezone.utc).isoformat(),reason,p['id']))
    return True

with st.sidebar:
    st.header('Controls')
    mode=st.radio('Trading mode',['Manual','Auto paper'],index=0 if setting('mode','Manual')=='Manual' else 1)
    save_setting('mode',mode)
    auto_enabled=st.toggle('Enable automatic paper entries',value=setting('auto_enabled','False')=='True',disabled=mode!='Auto paper')
    save_setting('auto_enabled',auto_enabled)
    qty=st.number_input('Contracts per entry',min_value=1,max_value=100,value=int(setting('qty','5')))
    save_setting('qty',qty)
    tp_c=st.slider('Take profit (contract cents above entry)',1,50,int(setting('tp','15')))
    sl_c=st.slider('Stop loss (contract cents below entry)',1,50,int(setting('sl','10')))
    save_setting('tp',tp_c);save_setting('sl',sl_c)
    max_open=st.slider('Maximum open positions',1,5,int(setting('max_open','1')))
    save_setting('max_open',max_open)
    refresh=st.selectbox('Refresh every', [5,10,15,30],index=2)
    if st.button('Refresh now',use_container_width=True):st.cache_data.clear();st.rerun()
    st.divider()
    st.caption('Auto mode only runs while this app session is awake. Local SQLite can be erased by Streamlit Cloud redeploy/restart.')

# Automatic rerun without browser-side script hacks.
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh*1000,key='paper_refresh')
except ImportError:st.warning('Install streamlit-autorefresh to enable timed updates.')

try:
    available=markets()
    now=datetime.now(timezone.utc)
    def parse_time(m):
        for k in ('close_time','expiration_time'):
            try:return datetime.fromisoformat(m[k].replace('Z','+00:00'))
            except (KeyError,TypeError,ValueError):pass
        return datetime.max.replace(tzinfo=timezone.utc)
    available=[m for m in available if parse_time(m)>now]
    available.sort(key=parse_time)
    current=available[0] if available else None
    signal=btc_signal()
except Exception as e:
    st.error('Market feed unavailable: '+str(e));st.stop()

if not current:
    st.warning('No open KXBTC15M market is currently available. No automatic entries will be made.')
else:
    ticker=current['ticker']
    st.subheader('Current market · '+ticker)
    try:m,q=quotes(ticker)
    except Exception as e:st.error('Quote feed unavailable: '+str(e));st.stop()
    seconds=max(0,int((parse_time(current)-now).total_seconds()))
    a,b,c,d,e=st.columns(5)
    a.metric('BTC reference',f"${signal['price']:,.2f}")
    b.metric('YES bid / ask',f"{q['YES']['bid'] if q['YES']['bid'] is not None else '—'} / {q['YES']['ask'] if q['YES']['ask'] is not None else '—'}")
    c.metric('NO bid / ask',f"{q['NO']['bid'] if q['NO']['bid'] is not None else '—'} / {q['NO']['ask'] if q['NO']['ask'] is not None else '—'}")
    d.metric('Time left',f'{seconds//60:02d}:{seconds%60:02d}')
    e.metric('Technical bias',signal['side'])
    st.caption(f"Signal: {signal['up']} bullish / {signal['down']} bearish points · RSI {signal['rsi']:.1f}. This is not a calibrated win probability.")

    # Manage open positions even after the active market changes.
    positions=open_positions()
    for p in positions:
        try:
            pm,pq=quotes(p['ticker'])
            bid=pq[p['side']]['bid']
            if bid is not None:
                if bid>=p['tp']:sell(p,bid,'TAKE PROFIT')
                elif bid<=p['sl']:sell(p,bid,'STOP LOSS')
        except Exception:pass

    # Auto-entry at most once per market ticker, never at the very end.
    positions=open_positions()
    with conn() as db:
        already=db.execute('SELECT COUNT(*) AS n FROM positions WHERE ticker=?',(ticker,)).fetchone()['n']>0
    if mode=='Auto paper' and auto_enabled and signal['side']!='WAIT' and not already and len(positions)<max_open and seconds>60:
        side=signal['side'];ask=q[side]['ask']
        if ask is not None and .05<=ask<=.95:
            err=buy(ticker,side,int(qty),ask,min(.99,ask+tp_c/100),max(.01,ask-sl_c/100))
            if err:st.warning('Auto entry skipped: '+err)
            else:st.success(f'Auto paper BUY: {qty} {side} at ${ask:.2f} · {ticker}')

    st.subheader('Place a manual paper trade')
    side=st.radio('Contract',['YES','NO'],horizontal=True)
    ask=q[side]['ask']
    st.write(f"Current {side} ask: **{('$%.2f'%ask) if ask is not None else 'Unavailable'}** · {qty} contracts")
    if st.button('BUY PAPER CONTRACT',type='primary',use_container_width=True,disabled=(ask is None or seconds<20)):
        err=buy(ticker,side,int(qty),ask,min(.99,ask+tp_c/100),max(.01,ask-sl_c/100))
        if err:st.error(err)
        else:st.success('Paper trade opened');st.rerun()

st.subheader('Paper account')
positions=open_positions()
value=cash()
a,b,c=st.columns(3)
a.metric('Available cash',f'${value:,.2f}')
b.metric('Open positions',len(positions))
c.metric('Starting balance',f"${float(setting('starting_cash','1000')):,.2f}")
if positions:
    for p in positions:
        with st.container(border=True):
            st.write(f"**#{p['id']} · {p['side']} · {p['ticker']}** · {p['qty']} contracts · Entry ${p['entry']:.2f} · TP ${p['tp']:.2f} · SL ${p['sl']:.2f}")
            try:
                pm,pq=quotes(p['ticker']);bid=pq[p['side']]['bid']
                if bid is not None:st.caption(f"Current bid ${bid:.2f} · estimated exit P/L ${p['qty']*(bid-p['entry'])-p['entry_fee']-fee(bid,p['qty']):+.2f}")
            except Exception:bid=None
            if st.button('Sell paper position at current bid',key='sell'+str(p['id']),disabled=bid is None):
                sell(p,bid,'MANUAL');st.rerun()
else:st.info('No open paper trades.')

st.subheader('Trade history')
rows=all_positions()
if rows:
    import pandas as pd
    st.dataframe(pd.DataFrame(rows)[['id','ticker','side','qty','entry','tp','sl','status','exit','pnl','reason']],hide_index=True,use_container_width=True)
else:st.caption('No trades yet.')

st.divider()
st.caption('Paper-only: no Kalshi authentication or real orders. Quoted prices are snapshots, not guaranteed fills. Exit monitoring happens on page refresh; gaps can skip targets. Expired markets are not automatically credited as wins or losses until official settlement is implemented. The simulator estimates fees and does not model partial fills or slippage.')
st.caption('Streamlit Community Cloud filesystem is ephemeral. Paper history is stored in SQLite locally and can disappear on restart/redeploy. For continuous autonomous trading, persistent hosting and database are required.')
