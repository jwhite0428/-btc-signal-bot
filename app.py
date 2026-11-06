import streamlit as st
import requests, pandas as pd, numpy as np, plotly.graph_objects as go
from datetime import datetime, timezone
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title='SENTINEL | BTC 15m',page_icon='🟢',layout='wide',initial_sidebar_state='collapsed')
st.markdown('''<style>
.stApp{background:#090b10;color:#e8edf5}.block-container{max-width:1240px;padding-top:12px;padding-bottom:40px}header[data-testid="stHeader"]{background:transparent}section[data-testid="stSidebar"]{background:#10141d}
[data-testid="stMetric"]{background:#11151e;border:1px solid #222936;border-radius:12px;padding:11px 13px;min-height:90px}[data-testid="stMetricLabel"]{color:#8d98aa;font-size:11px;text-transform:uppercase;letter-spacing:1px}[data-testid="stMetricValue"]{font-size:20px;font-weight:750}
[data-testid="stVerticalBlockBorderWrapper"]{border-color:#242b37!important;background:#11151d!important;border-radius:12px!important}
div.stButton>button{background:#1c222e;border:1px solid #2a3343;border-radius:9px;color:#f2f5fa}div.stButton>button[kind="primary"]{background:#238b53;color:white;border:0}h1,h2,h3{letter-spacing:-.5px}h3{font-size:16px}.stCaption{color:#8590a1}
.signal{font-size:32px;font-weight:900;letter-spacing:-1.4px;line-height:1.1}.up{color:#31d975}.down{color:#ff5d70}.wait{color:#e8b950}.bar{height:7px;border-radius:9px;background:#29303b;overflow:hidden}.fill{height:100%;background:#30d772}.tiny{font-size:11px;color:#8995a8;letter-spacing:.4px}.summary{border:1px solid #262d39;background:#10141c;border-radius:11px;padding:10px 13px;margin:8px 0}
</style>''',unsafe_allow_html=True)
BASE='https://api.elections.kalshi.com/trade-api/v2'
S=requests.Session(); S.headers.update({'User-Agent':'SentinelPaperDemo/1.0'})
@st.cache_data(ttl=10,show_spinner=False)
def markets():
    r=S.get(BASE+'/markets',params={'series_ticker':'KXBTC15M','status':'open','limit':100},timeout=12);r.raise_for_status()
    return r.json().get('markets',[])
@st.cache_data(ttl=8,show_spinner=False)
def orderbook(ticker):
    r=S.get(BASE+'/markets/'+ticker+'/orderbook',timeout=12);r.raise_for_status();return r.json()
@st.cache_data(ttl=10,show_spinner=False)
def candles():
    # Coinbase public 1-minute candles, max 300 bars per call
    r=S.get('https://api.exchange.coinbase.com/products/BTC-USD/candles',params={'granularity':60},timeout=15);r.raise_for_status()
    d=pd.DataFrame(r.json(),columns=['ts','low','high','open','close','volume'])
    if d.empty:raise ValueError('BTC feed returned no candles')
    d=d.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    d['time']=pd.to_datetime(d.ts,unit='s',utc=True)
    return d

def bids(ob, side):
    """Normalize Kalshi integer-cent, dollar-string and fixed-point orderbooks.

    Public API versions may wrap levels under orderbook, orderbook_fp, or
    orderbook_dollars. No fabricated quote is returned if a side is empty.
    """
    if not isinstance(ob, dict):
        return []
    raw = None
    unit = None
    for key, scale in ((side+'_dollars', 1.0), (side, 0.01)):
        if key in ob and ob[key] is not None:
            raw, unit = ob[key], scale
            break
    if raw is None:
        return []
    result = []
    for level in raw:
        try:
            if isinstance(level, dict):
                if level.get('price_dollars') is not None:
                    price = float(level['price_dollars'])
                elif level.get('price') is not None:
                    price = float(level['price']) * unit
                else:
                    continue
                qty = float(level.get('quantity', level.get('count', level.get('quantity_fp', 0))))
            else:
                price = float(level[0]) * unit
                qty = float(level[1])
            if 0 < price < 1 and qty > 0:
                result.append((price, qty))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return result

def normalize_orderbook(payload):
    """Kalshi can return either orderbook or orderbook_fp envelope."""
    if not isinstance(payload, dict):
        return {}
    for key in ('orderbook_fp', 'orderbook_dollars', 'orderbook'):
        if isinstance(payload.get(key), dict):
            return payload[key]
    return payload

def quote(ob):
    ob = normalize_orderbook(ob)
    yes = bids(ob, 'yes')
    no = bids(ob, 'no')
    yes_bid = max((price for price, quantity in yes), default=None)
    no_bid = max((price for price, quantity in no), default=None)
    # A YES ask can only be inferred from an available NO bid, and vice versa.
    return {
        'YES': {'bid': yes_bid, 'ask': round(1-no_bid, 4) if no_bid is not None else None},
        'NO': {'bid': no_bid, 'ask': round(1-yes_bid, 4) if yes_bid is not None else None},
    }

def indicators(df):
    x=df.copy();c=x.close;h=x.high;l=x.low
    for n in (12,25,50):x[f'ema{n}']=c.ewm(span=n,adjust=False).mean()
    change=c.diff();up=change.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();dn=(-change.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean();x['rsi']=100-100/(1+up/dn.replace(0,np.nan))
    x['macd']=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean();x['macds']=x.macd.ewm(span=9,adjust=False).mean()
    mid=c.rolling(20).mean();std=c.rolling(20).std();x['bbm']=mid;x['bbu']=mid+2*std;x['bbl']=mid-2*std
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1);x['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    u=h.diff();d=-l.diff();pdm=u.where((u>d)&(u>0),0);mdm=d.where((d>u)&(d>0),0)
    x['pdi']=100*pdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan);x['mdi']=100*mdm.ewm(alpha=1/14,adjust=False).mean()/x.atr.replace(0,np.nan)
    dx=100*(x.pdi-x.mdi).abs()/(x.pdi+x.mdi).replace(0,np.nan);x['adx']=dx.ewm(alpha=1/14,adjust=False).mean()
    x['tenkan']=(h.rolling(9).max()+l.rolling(9).min())/2;x['kijun']=(h.rolling(26).max()+l.rolling(26).min())/2
    # Standard SuperTrend using previous bands and close
    hl=(h+l)/2;bu=hl+3*x.atr;bl=hl-3*x.atr;fu=bu.copy();fl=bl.copy();trend=np.ones(len(x),dtype=bool)
    for i in range(1,len(x)):
        if pd.notna(fu.iloc[i-1]) and (bu.iloc[i]>=fu.iloc[i-1] and c.iloc[i-1]<=fu.iloc[i-1]):fu.iloc[i]=fu.iloc[i-1]
        if pd.notna(fl.iloc[i-1]) and (bl.iloc[i]<=fl.iloc[i-1] and c.iloc[i-1]>=fl.iloc[i-1]):fl.iloc[i]=fl.iloc[i-1]
        trend[i]=(c.iloc[i]>fu.iloc[i-1]) if not trend[i-1] else (c.iloc[i]>=fl.iloc[i-1])
    x['supertrend']=trend
    x['volavg']=x.volume.rolling(20).mean();x['rangehi']=h.rolling(100).max();x['rangelo']=l.rolling(100).min()
    return x

def votes(x):
    r=x.iloc[-2];p=x.iloc[-3];v=[]
    def add(name,side,reading):v.append((name,side,reading))
    def side(a,b):return 'UP' if a>b else 'DOWN' if a<b else 'FLAT'
    add('EMA 12 / 25 / 50','UP' if r.ema12>r.ema25>r.ema50 else 'DOWN' if r.ema12<r.ema25<r.ema50 else 'FLAT',f'{r.ema12:,.1f} / {r.ema25:,.1f} / {r.ema50:,.1f}')
    add('DMI +DI / -DI',side(r.pdi,r.mdi),f'+DI {r.pdi:.1f} / -DI {r.mdi:.1f}')
    add('ADX strength',side(r.pdi,r.mdi) if r.adx>=20 else 'FLAT',f'{r.adx:.1f}')
    add('SuperTrend','UP' if r.supertrend else 'DOWN','Uptrend' if r.supertrend else 'Downtrend')
    add('RSI 14','UP' if 52<=r.rsi<75 else 'DOWN' if 25<r.rsi<=48 else 'FLAT',f'{r.rsi:.1f}')
    add('MACD',side(r.macd,r.macds),f'Histogram {r.macd-r.macds:+.2f}')
    add('Bollinger midline',side(r.close,r.bbm),f'Mid {r.bbm:,.1f}')
    add('Ichimoku Tenkan/Kijun',side(r.tenkan,r.kijun),f'{r.tenkan:,.1f} / {r.kijun:,.1f}')
    add('Volume confirmation','UP' if r.close>r.open and r.volume>r.volavg else 'DOWN' if r.close<r.open and r.volume>r.volavg else 'FLAT',f'{r.volume:.2f} BTC')
    add('Candle momentum',side(r.close,r.open),f'{r.close-r.open:+.1f}')
    add('ATR breakout','UP' if r.close>p.high else 'DOWN' if r.close<p.low else 'FLAT',f'ATR {r.atr:.1f}')
    fib=(r.close-r.rangelo)/(r.rangehi-r.rangelo) if r.rangehi>r.rangelo else .5
    add('Fibonacci position','UP' if fib>.618 else 'DOWN' if fib<.382 else 'FLAT',f'{fib*100:.1f}%')
    add('Short-term structure',side(r.close,r.ema12),f'EMA12 {r.ema12:,.1f}')
    u=sum(s=='UP' for _,s,_ in v);d=sum(s=='DOWN' for _,s,_ in v)
    signal='UP' if u>=8 and u-d>=4 else 'DOWN' if d>=8 and d-u>=4 else 'WAIT'
    return signal,u,d,v

def money(v):return '—' if v is None else f'{v*100:.1f}¢'

def contract_side(signal):
    return {'UP':'YES','DOWN':'NO','YES':'YES','NO':'NO'}.get(signal)

def when(m):
    for k in ('close_time','expiration_time'):
        try:
            if m.get(k):return pd.Timestamp(m[k]).tz_convert('UTC')
        except Exception:pass
    return pd.Timestamp.max.tz_localize('UTC')

for k,val in {'cash':1000.,'positions':[],'history':[],'locked':None,'last_auto':None,'last_market':None,'settlement_note':None,'interval_log':{},'blocked_reasons':{},'auto_attempts':set()}.items():
    if k not in st.session_state:st.session_state[k]=val

# Worker is independent of Streamlit session state. Configure URL/token in
# Streamlit secrets; never expose the token in the rendered page.
try:
    worker_url=str(st.secrets.get('SENTINEL_WORKER_URL','')).rstrip('/')
    worker_token=str(st.secrets.get('SENTINEL_WORKER_TOKEN',''))
except Exception:
    worker_url='';worker_token=''
worker_configured=bool(worker_url and worker_token)
worker_snapshot=None
if worker_configured:
    try:
        rr=requests.get(worker_url+'/api/snapshot',headers={'Authorization':'Bearer '+worker_token},timeout=10)
        rr.raise_for_status();worker_snapshot=rr.json()
    except Exception as exc:
        st.warning('24/7 worker dashboard unavailable. Check Render service and Streamlit secrets. '+str(exc)[:150])

st.subheader('24/7 CLOUD WORKER · PERSISTENT PAPER TRADES')
if worker_snapshot:
    stats=worker_snapshot['score'];meta=worker_snapshot['meta']
    wc=st.columns(5)
    wc[0].metric('WORKER CASH',f"${worker_snapshot['paper_cash']:,.2f}")
    wc[1].metric('WORKER OPEN',len(worker_snapshot['open_positions']))
    wc[2].metric('WORKER WIN RATE',f"{stats['win_rate_pct']:.1f}%" if stats['win_rate_pct'] is not None else 'N/A')
    wc[3].metric('WORKER NET P/L',f"${stats['net_pnl']:+.2f}")
    wc[4].metric('WORKER INTERVALS',len(worker_snapshot['intervals']))
    st.caption('Last successful tick: '+str(meta.get('last_tick','never'))+' UTC · '+str(meta.get('last_ticker','no market'))+' · Signal '+str(meta.get('last_signal','—')))
    if meta.get('last_error'):st.error('Worker error: '+str(meta['last_error']))
    if worker_snapshot['open_positions']:
        with st.expander('Worker open positions',expanded=True):st.dataframe(pd.DataFrame(worker_snapshot['open_positions']),use_container_width=True,hide_index=True)
    with st.expander('Persistent worker trades and interval decisions'):
        if worker_snapshot['trades']:
            st.dataframe(pd.DataFrame(worker_snapshot['trades']),use_container_width=True,hide_index=True)
            st.download_button('Export worker trades CSV',pd.DataFrame(worker_snapshot['trades']).to_csv(index=False),'sentinel_worker_trades.csv','text/csv')
        if worker_snapshot['intervals']:
            st.dataframe(pd.DataFrame(worker_snapshot['intervals']),use_container_width=True,hide_index=True)
            st.download_button('Export worker intervals CSV',pd.DataFrame(worker_snapshot['intervals']).to_csv(index=False),'sentinel_worker_intervals.csv','text/csv')
        if worker_snapshot['events']:st.dataframe(pd.DataFrame(worker_snapshot['events']),use_container_width=True,hide_index=True)
    st.caption('Worker settings are configured in Render environment variables. Local sliders below do NOT change the 24/7 worker.')
elif not worker_configured:
    st.info('24/7 worker not connected. Add SENTINEL_WORKER_URL and SENTINEL_WORKER_TOKEN in Streamlit Secrets after deploying Render.')

with st.sidebar:
    st.header('SENTINEL SETTINGS')
    auto_refresh=st.toggle('Live auto-refresh',value=True)
    refresh=st.select_slider('Refresh every',options=[5,10,15,30],value=10)
    if auto_refresh:st_autorefresh(interval=refresh*1000,key='live')
    if st.button('↻ Refresh now',use_container_width=True):st.cache_data.clear();st.rerun()
    st.divider()
    mode=st.radio('Trading mode',['Manual paper','Automatic paper'],index=0)
    auto_enabled=st.toggle('Enable automatic entries',value=False,disabled=worker_configured or mode!='Automatic paper')
    if worker_configured:st.caption('Disabled: 24/7 cloud worker handles entries independently. Local settings do not affect it.')
    qty=st.number_input('Contracts per entry',min_value=1,max_value=1000,value=5)
    max_entry=st.slider('Maximum entry ask (¢)',1,99,65)
    min_votes=st.slider('Minimum directional votes (of 13)',7,12,10)
    min_lead=st.slider('Minimum vote lead',2,10,5)
    min_time=st.slider('Minimum seconds left for entry',30,600,180,step=30)
    max_spread=st.slider('Maximum contract spread (¢)',1,40,12)
    min_adx=st.slider('Minimum ADX (trend strength)',10,40,20)
    require_positive_ev=st.toggle('Require conservative probability edge',value=False,help='Experimental proxy, not a calibrated probability. Default OFF to avoid false precision.')
    st.caption('These are selective filters, not a validated profitable strategy.')
    st.divider()
    tp=st.number_input('Take profit (¢ above entry)',min_value=0,max_value=99,value=20)
    sl=st.number_input('Stop loss (¢ below entry)',min_value=0,max_value=99,value=15)
    opposite=st.toggle('Opposing-signal stop',value=True)
    start_buffer=st.number_input('Start buffer (seconds)',min_value=0,max_value=600,value=0,step=15)
    st.divider()
    if st.button('Reset paper balance and history',use_container_width=True):
        st.session_state.cash=1000.;st.session_state.positions=[];st.session_state.history=[];st.session_state.locked=None;st.session_state.last_auto=None;st.session_state.interval_log={};st.session_state.blocked_reasons={};st.session_state.auto_attempts=set();st.rerun()
    st.caption('Paper-only. No account connection or real orders. Browser session state can reset.')

try:
    ms=markets();ms=sorted([m for m in ms if m.get('ticker')],key=when)
    active=next((m for m in ms if when(m)>pd.Timestamp.now(tz='UTC')),None)
    x=indicators(candles());sig,u,d,ev=votes(x)
except Exception as e:
    st.error(f'Public market feed unavailable: {e}');st.info('Refresh or retry later. No simulated trades were placed.');st.stop()

if not active:
    st.warning('No open KXBTC15M market was returned by the Kalshi API. The app will not invent prices or select another series.')
    st.caption('BTC chart may still be available, but trading is paused.')
    st.stop()
ticker=active['ticker'];now=pd.Timestamp.now(tz='UTC');remaining=max(0,int((when(active)-now).total_seconds()))
# The strategy uses completed candles only. The 13 votes are correlated and
# are NOT a calibrated probability of a Kalshi settlement outcome.
trend_adx=float(x.adx.iloc[-2]) if pd.notna(x.adx.iloc[-2]) else 0.0
lead=abs(u-d)
strong_side='UP' if u>d else 'DOWN' if d>u else 'WAIT'

try:
    raw_orderbook = orderbook(ticker)
    normalized_book = normalize_orderbook(raw_orderbook)
    q = quote(raw_orderbook)
except Exception as e:
    st.error(f'Could not retrieve Kalshi order book: {e}')
    st.stop()
if all(q[side][field] is None for side in ('YES', 'NO') for field in ('bid', 'ask')):
    st.warning('Kalshi returned no usable YES/NO bid levels for this contract. Paper entries and exits are paused; the bot will not invent prices.')
    with st.expander('Quote diagnostics (no account details)'):
        st.write('Market ticker:', ticker)
        st.write('Orderbook response keys:', list(raw_orderbook.keys()) if isinstance(raw_orderbook, dict) else type(raw_orderbook).__name__)
        st.write('Orderbook fields:', list(normalized_book.keys()) if isinstance(normalized_book, dict) else type(normalized_book).__name__)
        st.write('YES levels parsed:', len(bids(normalized_book, 'yes')))
        st.write('NO levels parsed:', len(bids(normalized_book, 'no')))



def entry_filter(side, ask, bid, current_remaining):
    reasons=[]
    if sig not in ('UP','DOWN') or side != sig:reasons.append('No confirmed directional signal')
    if max(u,d)<min_votes:reasons.append(f'Only {max(u,d)}/{13} agreeing votes; need {min_votes}')
    if lead<min_lead:reasons.append(f'Vote lead {lead}; need {min_lead}')
    if trend_adx<min_adx:reasons.append(f'ADX {trend_adx:.1f}; need {min_adx}')
    if ask is None or bid is None:reasons.append('Missing live Kalshi bid or ask')
    else:
        if not (0<bid<=ask<1):reasons.append('Invalid or crossed quote')
        if ask*100>max_entry:reasons.append(f'Ask {ask*100:.1f}¢ exceeds {max_entry}¢')
        if (ask-bid)*100>max_spread:reasons.append(f'Spread {(ask-bid)*100:.1f}¢ exceeds {max_spread}¢')
    if current_remaining<min_time:reasons.append(f'Only {current_remaining}s left; need {min_time}s')
    if current_remaining<=start_buffer:reasons.append('Within start buffer')
    # No fake win-probability estimate: this optional setting is intentionally
    # not used until a separately calibrated, out-of-sample model is provided.
    if require_positive_ev:reasons.append('Probability model not calibrated; EV gate unavailable')
    return reasons

entry_side=contract_side(sig)
entry_ask=q.get(entry_side,{}).get('ask') if entry_side else None
entry_bid=q.get(entry_side,{}).get('bid') if entry_side else None
entry_reasons=entry_filter(sig,entry_ask,entry_bid,remaining)
st.session_state.interval_log.setdefault(ticker,{'market':ticker,'first_seen_utc':now.isoformat(),'decision':'NOT ENTERED','signal':sig,'votes_up':u,'votes_down':d,'reason':'', 'outcome':'UNRESOLVED'})
st.session_state.interval_log[ticker]['reason']='; '.join(entry_reasons) if entry_reasons else 'Qualified quote and signal'
st.session_state.interval_log[ticker]['signal']=sig
st.session_state.interval_log[ticker]['votes_up']=u
st.session_state.interval_log[ticker]['votes_down']=d

if st.session_state.last_market!=ticker:
    st.session_state.locked=None;st.session_state.last_market=ticker
if st.session_state.locked is None and sig in ('UP','DOWN') and not entry_reasons:st.session_state.locked=sig
locked=st.session_state.locked or 'WAIT'
last_exit_event=None
# Do not simulate fills from missing prices or prices in other contracts.
# Open positions are NOT auto-settled without authoritative outcome data.
for p in list(st.session_state.positions):
    if p['ticker']!=ticker:continue
    contract=contract_side(p['side'])
    bid=q.get(contract,{}).get('bid') if contract else None
    if bid is None:continue
    take=min(.99,p['entry']+p.get('tp_cents',tp)/100);stop=max(.01,p['entry']-p.get('sl_cents',sl)/100)
    reason='TAKE PROFIT' if tp>0 and bid>=take else 'STOP LOSS' if sl>0 and bid<=stop else 'OPPOSING SIGNAL' if opposite and sig in ('UP','DOWN') and contract_side(sig)!=contract else None
    if reason:
        fee=round(p['qty']*bid*(1-bid)*.07,2);proceeds=round(p['qty']*bid-fee,2)
        st.session_state.cash+=proceeds;st.session_state.positions.remove(p)
        st.session_state.history.insert(0,{**p,'exit':bid,'exit_fee':fee,'pnl':round(proceeds-p['cost'],2),'reason':reason})
        last_exit_event=(reason,p['side'],bid)
        if p['ticker'] in st.session_state.interval_log:
            row=st.session_state.interval_log[p['ticker']]
            row['outcome']='WIN' if proceeds-p['cost']>0 else 'LOSS' if proceeds-p['cost']<0 else 'BREAKEVEN'
            row['net_pnl']=round(proceeds-p['cost'],2)
            row['exit_reason']=reason
        st.toast(f"Paper {reason}: {p['side']} {money(bid)}",icon='🔔')

if not worker_configured and mode=='Automatic paper' and auto_enabled and not entry_reasons and st.session_state.last_auto!=ticker and ticker not in st.session_state.auto_attempts and not any(p['ticker']==ticker for p in st.session_state.positions):
    contract=contract_side(sig)
    ask=q.get(contract,{}).get('ask') if contract else None
    if ask is not None and ask*100<=max_entry:
        fee=round(qty*ask*(1-ask)*.07,2);cost=round(qty*ask+fee,2)
        if cost<=st.session_state.cash:
            st.session_state.cash-=cost;st.session_state.positions.append({'ticker':ticker,'side':contract,'qty':int(qty),'entry':ask,'fee':fee,'cost':cost,'source':'AUTO','tp_cents':int(tp),'sl_cents':int(sl)});st.session_state.last_auto=ticker
            st.session_state.interval_log[ticker]['decision']='AUTO ENTRY'
            st.session_state.interval_log[ticker]['entry_ask']=ask
            st.session_state.interval_log[ticker]['entry_time_utc']=now.isoformat()
            st.session_state.auto_attempts.add(ticker)
            st.toast(f'Paper AUTO BUY {sig} at {money(ask)}',icon='🟢')

# Action banner uses executable Kalshi contract quotes, not BTC spot prices.
current_position=next((p for p in st.session_state.positions if p['ticker']==ticker),None)
action='HOLD / WAIT'
action_detail='Waiting for a qualifying signal and a tradable Kalshi quote.'
action_class='wait'
if last_exit_event:
    reason,exit_side,exit_bid=last_exit_event
    action=('TAKE PROFIT / EXIT' if reason=='TAKE PROFIT' else 'STOP LOSS / EXIT' if reason=='STOP LOSS' else 'EXIT · OPPOSING SIGNAL')
    action_class='up' if reason=='TAKE PROFIT' else 'down'
    action_detail=f"Simulated {reason.lower()} completed for {exit_side} at Kalshi bid {money(exit_bid)}. See paper trade history."
elif current_position:
    p=current_position
    contract=contract_side(p['side'])
    current_bid=q.get(contract,{}).get('bid') if contract else None
    take=min(.99,p['entry']+p.get('tp_cents',tp)/100)
    stop=max(.01,p['entry']-p.get('sl_cents',sl)/100)
    if current_bid is None:
        action_detail=f"OPEN {contract} · entry {money(p['entry'])} · no current Kalshi bid; cannot simulate exit."
    elif p.get('tp_cents',tp)>0 and current_bid>=take:
        action='TAKE PROFIT / EXIT';action_class='up'
        action_detail=f"{contract} bid {money(current_bid)} reached target {money(take)}. The paper exit is processed on refresh."
    elif p.get('sl_cents',sl)>0 and current_bid<=stop:
        action='STOP LOSS / EXIT';action_class='down'
        action_detail=f"{contract} bid {money(current_bid)} reached stop {money(stop)}. The paper exit is processed on refresh."
    else:
        action_detail=f"HOLD {contract} · entry {money(p['entry'])} · current bid {money(current_bid)} · TP {money(take)} · SL {money(stop)}."
else:
    entry_contract=contract_side(sig)
    entry_ask=q.get(entry_contract,{}).get('ask') if entry_contract else None
    if entry_contract and not entry_reasons and st.session_state.cash>=(qty*entry_ask+round(qty*entry_ask*(1-entry_ask)*.07,2)) and st.session_state.last_auto!=ticker:
        action='ENTER NOW';action_class='up'
        action_detail=f"Potential {sig} entry: buy {entry_contract} at Kalshi ask {money(entry_ask)}. Evidence {max(u,d)}/13; not a win probability."
    elif entry_contract and entry_ask is not None and entry_ask*100>max_entry:
        action_detail=f"{sig} signal, but Kalshi {entry_contract} ask {money(entry_ask)} exceeds your {max_entry}¢ maximum."
    elif entry_reasons:
        action_detail='Selective entry filter: '+ '; '.join(entry_reasons[:3])
    elif st.session_state.last_auto==ticker:
        action_detail='Already opened an automatic paper trade in this contract; waiting for next market.'
st.markdown(f'<div class="summary"><div class="signal {action_class}">{action}</div><div style="font-size:13px;margin-top:7px">{action_detail}</div><div class="tiny" style="margin-top:7px">PAPER MODE · NO REAL ORDERS · QUOTES CAN CHANGE BETWEEN REFRESHES</div></div>',unsafe_allow_html=True)

cl='up' if locked=='UP' else 'down' if locked=='DOWN' else 'wait'
st.markdown(f'<div class="signal {cl}">{locked} · {"CALL" if locked=="UP" else "PUT" if locked=="DOWN" else "NO SIGNAL"} LOCKED · BUY ASK {money(q.get(contract_side(locked),{}).get('ask')) if contract_side(locked) else '—'}</div>',unsafe_allow_html=True)
st.caption(f'{ticker} · {active.get("title", "BTC 15-minute market")} · Current {locked} ask {money(q.get(contract_side(locked),{}).get('ask')) if contract_side(locked) else '—'} · Evidence {max(u,d)}/13 indicators (not a win probability)')
st.markdown(f'<div class="summary"><span class="tiny">TAKE PROFIT {"OFF · HOLDING TO SETTLEMENT" if tp==0 else str(tp)+"¢ ABOVE ENTRY"} &nbsp; | &nbsp; STOP LOSS {sl}¢ &nbsp; | &nbsp; START BUFFER {start_buffer}s &nbsp; | &nbsp; OPPOSING-SIGNAL STOP {"ON" if opposite else "OFF"}</span></div>',unsafe_allow_html=True)
cols=st.columns(6)
cols[0].metric('UP ASK / BID',f'{money(q["YES"]["ask"])} / {money(q["YES"]["bid"])}')
cols[1].metric('DOWN ASK / BID',f'{money(q["NO"]["ask"])} / {money(q["NO"]["bid"])}')
strike=active.get('floor_strike') or active.get('strike_price') or active.get('strike')
cols[2].metric('STRIKE',str(strike) if strike is not None else 'See contract')
cols[3].metric('BTC',f'${x.close.iloc[-1]:,.2f}')
cols[4].metric('TIME LEFT',f'{remaining//60:02d}:{remaining%60:02d}')
cols[5].metric('REGIME','TRENDING' if x.adx.iloc[-2]>=20 else 'RANGING')
st.caption('SENTINEL BTC CHART · 1m CANDLES · INDICATORS FROM COMPLETED CANDLES · PUBLIC COINBASE BTC/USD FEED')
left,right=st.columns([4,1]);zones=right.toggle('S/D ZONES',value=True);labels=right.toggle('LABELS',value=True)
fig=go.Figure();fig.add_trace(go.Candlestick(x=x.time,open=x.open,high=x.high,low=x.low,close=x.close,name='BTC',increasing_line_color='#30d67a',decreasing_line_color='#ff596b'))
for n,color in [(12,'#e3c46a'),(25,'#ad8ce7'),(50,'#67b3ee')]:fig.add_trace(go.Scatter(x=x.time,y=x[f'ema{n}'],name=f'EMA {n}',line=dict(color=color,width=1.5)))
if zones:
    lo=float(x.low.tail(80).min());hi=float(x.high.tail(80).max())
    fig.add_hrect(y0=lo,y1=lo+(hi-lo)*.1,fillcolor='#ed5260',opacity=.16,line_width=0)
    fig.add_hrect(y0=hi-(hi-lo)*.1,y1=hi,fillcolor='#2dcc76',opacity=.12,line_width=0)
fig.update_layout(template='plotly_dark',height=450,margin=dict(l=0,r=0,t=4,b=0),paper_bgcolor='#090b10',plot_bgcolor='#090b10',xaxis_rangeslider_visible=False,legend=dict(orientation='h',y=-.06),font=dict(color='#9aa6b8'))
st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False})
fig2=go.Figure()
for col,name,color in [('pdi','+DI','#3ad978'),('mdi','-DI','#ff536c'),('adx','ADX','#a98be9')]:fig2.add_trace(go.Scatter(x=x.time,y=x[col],name=name,line=dict(color=color,width=2)))
fig2.update_layout(template='plotly_dark',height=120,margin=dict(l=0,r=0,t=0,b=0),paper_bgcolor='#090b10',plot_bgcolor='#090b10',legend=dict(orientation='h',y=-.2),font=dict(color='#9aa6b8'))
st.plotly_chart(fig2,use_container_width=True,config={'displayModeBar':False})

l,r=st.columns([1,1],gap='medium')
with l:
    with st.container(border=True):
        st.subheader('Indicator evidence')
        for name,side,reading in ev:
            color='#30d675' if side=='UP' else '#ff5e70' if side=='DOWN' else '#a0aaba'
            st.markdown(f'<div style="display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #222936;padding:7px 0;font-size:12px"><span>{name}</span><span style="text-align:right;color:{color}">{side} · {reading}</span></div>',unsafe_allow_html=True)
with r:
    with st.container(border=True):
        st.subheader('Live evidence')
        strength=max(u,d)/13*100
        st.markdown(f'<div style="font-size:34px;font-weight:900;color:#30d675">{strength:.0f}% <span class="tiny">{sig} evidence alignment</span></div>',unsafe_allow_html=True)
        st.caption('Not a calibrated win probability')
        for name,value in [('Trend indicators',int(100*(u if u>d else d)/13)),('Momentum indicators',int(100*sum(s==sig for _,s,_ in ev[4:10])/6) if sig!='WAIT' else 0),('Range and levels',int(100*sum(s==sig for _,s,_ in ev[10:])/3) if sig!='WAIT' else 0)]:
            st.markdown(f'<div class="tiny" style="margin-top:14px">{name} · {value}%</div><div class="bar"><div class="fill" style="width:{min(100,value)}%"></div></div>',unsafe_allow_html=True)
        st.write(f'UP votes: **{u}** · DOWN votes: **{d}** · FLAT: **{13-u-d}**')
        st.caption('Entries are simulated at Kalshi ask. Exits use Kalshi bid. No BTC spot entry/exit pricing.')

st.subheader('LEGACY SESSION PAPER TRADING · SEPARATE FROM 24/7 WORKER')
if worker_configured:st.info('The paper cash and trade history below belong to this browser session, not the cloud worker. Use the worker panel above for persistent results.')
a,b,c=st.columns(3);a.metric('PAPER CASH',f'${st.session_state.cash:,.2f}');b.metric('OPEN POSITIONS',len(st.session_state.positions));c.metric('CLOSED TRADES',len(st.session_state.history))
if mode=='Manual paper':
    a,b,c=st.columns([1,1,1]);side=a.selectbox('Contract',['YES','NO']);amount=b.number_input('Contracts',1,1000,int(qty));ask=q[side]['ask'];c.metric('LIVE ENTRY ASK',money(ask))
    if entry_reasons:st.warning('Selective strategy does not qualify: '+'; '.join(entry_reasons[:3])+'. Manual paper trade remains available as an override.')
    if st.button('BUY PAPER CONTRACTS',type='primary',use_container_width=True):
        if ask is None:st.error('No executable ask available')
        else:
            fee=round(amount*ask*(1-ask)*.07,2);cost=round(amount*ask+fee,2)
            if cost>st.session_state.cash:st.error('Insufficient paper balance')
            elif remaining<=start_buffer:st.error('Too close to market close for a new entry')
            else:
                st.session_state.cash-=cost;st.session_state.positions.append({'ticker':ticker,'side':side,'qty':int(amount),'entry':ask,'fee':fee,'cost':cost,'source':'MANUAL','tp_cents':int(tp),'sl_cents':int(sl)})
                st.session_state.interval_log[ticker]['decision']='MANUAL ENTRY'
                st.session_state.interval_log[ticker]['entry_ask']=ask
                st.session_state.interval_log[ticker]['entry_time_utc']=now.isoformat()
                st.rerun()
else:st.info('Automatic paper mode: enable entries in the sidebar. An ENTER NOW banner is a signal, not confirmation of an actual paper fill.')
if st.session_state.positions:
    st.subheader('Open paper positions')
    for i,p in enumerate(st.session_state.positions):
        contract=contract_side(p['side'])
        bid=q.get(contract,{}).get('bid') if p['ticker']==ticker and contract else None
        a,b,c,d=st.columns([3,1,1,1]);a.write(f"**{p['side']}** · {p['qty']} contracts · {p['ticker']}");b.metric('ENTRY',money(p['entry']));c.metric('BID',money(bid));
        if d.button('SELL PAPER',key=f'sell_{i}'):
            if bid is None:st.warning('No live bid for this contract. Cannot simulate sale or settle without verified outcome.')
            else:
                fee=round(p['qty']*bid*(1-bid)*.07,2);proceeds=round(p['qty']*bid-fee,2);st.session_state.cash+=proceeds;st.session_state.positions.pop(i)
                realized=round(proceeds-p['cost'],2)
                st.session_state.history.insert(0,{**p,'exit':bid,'exit_fee':fee,'pnl':realized,'reason':'MANUAL SELL'})
                row=st.session_state.interval_log.get(p['ticker'])
                if row is not None:
                    row['outcome']='WIN' if realized>0 else 'LOSS' if realized<0 else 'BREAKEVEN'
                    row['net_pnl']=realized;row['exit_reason']='MANUAL SELL'
                st.rerun()
if st.session_state.history:
    st.subheader('Paper trade history');st.dataframe(pd.DataFrame(st.session_state.history),hide_index=True,use_container_width=True)
st.caption('Paper trading only. Simulated fills are approximations; no queue, partial fills, or exact fee schedule. Streamlit session may reset. Expired positions are NOT automatically settled. Automatic trading runs only while the app is active and refreshing.')

st.subheader('SELECTIVE STRATEGY SCORECARD')
closed=st.session_state.history
wins=sum(float(t.get('pnl',0))>0 for t in closed)
losses=sum(float(t.get('pnl',0))<0 for t in closed)
breakeven=sum(float(t.get('pnl',0))==0 for t in closed)
resolved=wins+losses
net=sum(float(t.get('pnl',0)) for t in closed)
profit=sum(max(0,float(t.get('pnl',0))) for t in closed)
gross_loss=-sum(min(0,float(t.get('pnl',0))) for t in closed)
score=st.columns(5)
score[0].metric('WIN RATE',f'{wins/resolved*100:.1f}%' if resolved else 'N/A')
score[1].metric('WINS / LOSSES',f'{wins} / {losses}')
score[2].metric('NET PAPER P/L',f'${net:+.2f}')
score[3].metric('PROFIT FACTOR',f'{profit/gross_loss:.2f}' if gross_loss else 'N/A')
score[4].metric('TRACKED INTERVALS',len(st.session_state.interval_log))
st.caption('Win rate counts closed profitable vs losing paper trades only. Unresolved and open positions are excluded. Fees are approximate. A 75% win rate is not guaranteed.')
if st.session_state.interval_log:
    rows=list(st.session_state.interval_log.values())
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    st.download_button('Download interval log (CSV)',data=pd.DataFrame(rows).to_csv(index=False),file_name='sentinel_interval_log.csv',mime='text/csv')
if closed:
    st.download_button('Download closed paper trades (CSV)',data=pd.DataFrame(closed).to_csv(index=False),file_name='sentinel_closed_trades.csv',mime='text/csv')
st.info('Backtesting is NOT included. Results here are forward paper observations only, and will reset if Streamlit session state is lost. Open/expired contracts are not counted as wins or losses without an actual simulated exit.')
