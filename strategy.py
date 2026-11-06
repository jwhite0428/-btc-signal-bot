"""SENTINEL shared technical analysis. Extracted from user's v7 app."""
import numpy as np
import pandas as pd
from datetime import datetime, timezone
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

def contract_side(signal):
    return {'UP':'YES','DOWN':'NO','YES':'YES','NO':'NO'}.get(signal)

def when(m):
    for k in ('close_time','expiration_time'):
        try:
            if m.get(k):return pd.Timestamp(m[k]).tz_convert('UTC')
        except Exception:pass
    return pd.Timestamp.max.tz_localize('UTC')
