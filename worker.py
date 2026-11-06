"""Independent 24/7 PAPER-ONLY Kalshi monitor. No order placement or credentials."""
import json, logging, os, sqlite3, threading, time
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd
from strategy import bids, quote, indicators, votes, when

BASE='https://api.elections.kalshi.com/trade-api/v2'
DB=os.environ.get('SENTINEL_DB','/data/sentinel.sqlite3')
POLL=max(5,int(os.environ.get('POLL_SECONDS','15')))
MIN_VOTES=int(os.environ.get('MIN_VOTES','8'))
MIN_LEAD=int(os.environ.get('MIN_LEAD','4'))
MIN_ADX=float(os.environ.get('MIN_ADX','18'))
MAX_ENTRY=int(os.environ.get('MAX_ENTRY_CENTS','60'))
MAX_SPREAD=int(os.environ.get('MAX_SPREAD_CENTS','8'))
MIN_TIME=int(os.environ.get('MIN_SECONDS_LEFT','120'))
QTY=int(os.environ.get('PAPER_QTY','1'))
TP=int(os.environ.get('TP_CENTS','15'))
SL=int(os.environ.get('SL_CENTS','10'))
START_CASH=float(os.environ.get('PAPER_START_CASH','1000'))
MAX_STALE=int(os.environ.get('MAX_CANDLE_AGE_SECONDS','180'))
MAX_QUOTE_AGE=int(os.environ.get('MAX_QUOTE_AGE_SECONDS','40'))
# Experimental probability edge is not implemented: no calibrated probabilities.
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
HTTP=requests.Session();HTTP.headers.update({'User-Agent':'SentinelPaperWorker/1.0'})

def utcnow():return datetime.now(timezone.utc).isoformat()
def connect():
 Path(DB).parent.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(DB,timeout=30,isolation_level=None)
 db.row_factory=sqlite3.Row
 db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=30000')
 return db

def init():
 with connect() as db:
  db.executescript('''
 CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS intervals(ticker TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, signal TEXT, votes_up INTEGER, votes_down INTEGER, reason TEXT, decision TEXT NOT NULL DEFAULT 'SKIPPED');
 CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,ticker TEXT NOT NULL UNIQUE, side TEXT NOT NULL, qty INTEGER NOT NULL, entry REAL NOT NULL, entry_fee REAL NOT NULL, entry_time TEXT NOT NULL, exit REAL, exit_fee REAL, exit_time TEXT, pnl REAL, exit_reason TEXT, state TEXT NOT NULL DEFAULT 'OPEN');
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL);
 ''')
  db.execute('INSERT OR IGNORE INTO meta(key,value) VALUES (?,?)',('starting_cash',str(START_CASH)))

def event(db,kind,msg):
 db.execute('INSERT INTO events(at,kind,message) VALUES (?,?,?)',(utcnow(),kind,str(msg)[:1000]))

def fetch_json(path,params=None):
 r=HTTP.get(BASE+path,params=params,timeout=12);r.raise_for_status();return r.json()

def get_market():
 markets=fetch_json('/markets',{'series_ticker':'KXBTC15M','status':'open','limit':100}).get('markets',[])
 now=pd.Timestamp.now(tz='UTC')
 valid=[m for m in markets if m.get('ticker') and when(m)>now]
 return min(valid,key=when) if valid else None

def get_candles():
 r=HTTP.get('https://api.exchange.coinbase.com/products/BTC-USD/candles',params={'granularity':60},timeout=15);r.raise_for_status()
 d=pd.DataFrame(r.json(),columns=['ts','low','high','open','close','volume'])
 if len(d)<110:raise ValueError('Insufficient BTC candles')
 d=d.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
 d['time']=pd.to_datetime(d.ts,unit='s',utc=True)
 # Remove current, potentially incomplete 1-minute candle.
 cutoff=pd.Timestamp.now(tz='UTC').floor('min')
 d=d[d.time<cutoff].copy()
 if len(d)<105:raise ValueError('Insufficient completed BTC candles')
 age=(pd.Timestamp.now(tz='UTC')-d.time.iloc[-1]).total_seconds()
 if age>MAX_STALE:raise ValueError(f'Stale Coinbase candles: {age:.0f}s')
 return d

def book_for(ticker):
 payload=fetch_json('/markets/'+ticker+'/orderbook')
 raw=payload.get('orderbook',{})
 # Support new fixed-point orderbook field if provided by the API.
 if not raw and isinstance(payload.get('orderbook_fp'),dict):raw=payload['orderbook_fp']
 if not isinstance(raw,dict):raise ValueError('Unexpected orderbook response')
 return raw

def pricebook(ticker):
 raw=book_for(ticker)
 # bids() handles legacy integer-cent levels and modern dollar-denominated levels.
 q=quote(raw)
 return q,raw

def fee_est(qty,price):
 # Conservative approximation, not an authoritative Kalshi fee calculation.
 import math
 return math.ceil(100*qty*.07*price*(1-price))/100

def cash(db):
 start=float(db.execute('SELECT value FROM meta WHERE key=?',('starting_cash',)).fetchone()['value'])
 entries=db.execute('SELECT qty,entry,entry_fee,exit,exit_fee,state FROM trades').fetchall()
 return round(start-sum(t['qty']*t['entry']+t['entry_fee'] for t in entries)+sum(t['qty']*t['exit']-t['exit_fee'] for t in entries if t['state']=='CLOSED'),2)

def update_interval(db,ticker,sig,u,d,reasons):
 now=utcnow();reason='; '.join(reasons) if reasons else 'Qualified'
 db.execute('''INSERT INTO intervals(ticker,first_seen,last_seen,signal,votes_up,votes_down,reason) VALUES(?,?,?,?,?,?,?)
 ON CONFLICT(ticker) DO UPDATE SET last_seen=excluded.last_seen,signal=excluded.signal,votes_up=excluded.votes_up,votes_down=excluded.votes_down,reason=excluded.reason''',(ticker,now,now,sig,u,d,reason))

def settle_old(db,active_ticker):
 # Expired positions are not marked wins/losses until Kalshi reports a final result.
 rows=db.execute("SELECT * FROM trades WHERE state='OPEN' AND ticker!=?",(active_ticker,)).fetchall()
 for t in rows:
  try:
   m=fetch_json('/markets/'+t['ticker']).get('market',{})
   result=str(m.get('result') or '').lower()
   status=str(m.get('status') or '').lower()
   if result not in ('yes','no') or status not in ('settled','finalized'):
    continue
   exit_price=1. if result==t['side'].lower() else 0.
   # Settlement fee is zero here; this is a paper approximation.
   pnl=round(t['qty']*exit_price-(t['qty']*t['entry']+t['entry_fee']),2)
   db.execute("UPDATE trades SET exit=?,exit_fee=0,exit_time=?,pnl=?,exit_reason='SETTLEMENT',state='CLOSED' WHERE id=? AND state='OPEN'",(exit_price,utcnow(),pnl,t['id']))
   event(db,'SETTLEMENT',f"{t['ticker']} {t['side']} settled {result}, paper P/L ${pnl:+.2f}")
  except Exception as exc:logging.warning('Settlement check %s: %s',t['ticker'],exc)

def tick():
 market=get_market()
 if not market:raise ValueError('No open KXBTC15M market')
 ticker=market['ticker'];remaining=int((when(market)-pd.Timestamp.now(tz='UTC')).total_seconds())
 data=indicators(get_candles());sig,u,d,evidence=votes(data)
 # The original votes() uses fixed 8/4 threshold; recompute using user-configurable thresholds.
 lead=abs(u-d)
 side='UP' if u>d else 'DOWN' if d>u else 'WAIT'
 side_contract={'UP':'YES','DOWN':'NO'}.get(side)
 adx=float(data.adx.iloc[-2]) if pd.notna(data.adx.iloc[-2]) else 0.
 q,raw=pricebook(ticker)
 current=q.get(side_contract,{}) if side_contract else {}
 ask=current.get('ask');bid=current.get('bid')
 reasons=[]
 if max(u,d)<MIN_VOTES:reasons.append(f'votes {max(u,d)}/{MIN_VOTES}')
 if lead<MIN_LEAD:reasons.append(f'lead {lead}/{MIN_LEAD}')
 if adx<MIN_ADX:reasons.append(f'ADX {adx:.1f}<{MIN_ADX}')
 if remaining<MIN_TIME:reasons.append(f'time {remaining}s<{MIN_TIME}s')
 if ask is None or bid is None:reasons.append('missing live bid/ask')
 else:
  if not (0<bid<=ask<1):reasons.append('invalid bid/ask')
  if ask*100>MAX_ENTRY:reasons.append(f'ask {ask*100:.1f}c>{MAX_ENTRY}c')
  if (ask-bid)*100>MAX_SPREAD:reasons.append(f'spread {(ask-bid)*100:.1f}c>{MAX_SPREAD}c')
 # Prevent paper fills when orderbook cannot cover the requested size at best price.
 if side_contract and ask is not None:
  opposite='no' if side_contract=='YES' else 'yes'
  levels=bids(raw,opposite)
  best=max((p for p,n in levels),default=None)
  depth=sum(n for p,n in levels if best is not None and abs(p-best)<1e-7)
  if depth<QTY:reasons.append(f'best ask depth {depth:g}<{QTY}')
 with connect() as db:
  db.execute('BEGIN IMMEDIATE')
  settle_old(db,ticker)
  update_interval(db,ticker,side,u,d,reasons)
  open_position=db.execute("SELECT * FROM trades WHERE ticker=? AND state='OPEN'",(ticker,)).fetchone()
  if open_position:
   p=open_position;exit_bid=q.get(p['side'],{}).get('bid')
   if exit_bid is not None:
    target=min(.99,p['entry']+TP/100);stop=max(.01,p['entry']-SL/100)
    reason='TAKE PROFIT' if TP>0 and exit_bid>=target else 'STOP LOSS' if SL>0 and exit_bid<=stop else 'OPPOSING SIGNAL' if side_contract and side_contract!=p['side'] and max(u,d)>=MIN_VOTES and lead>=MIN_LEAD else None
    if reason:
     # Simulate full immediate fill only if visible best-bid depth suffices.
     depth=sum(n for price,n in bids(raw,p['side'].lower()) if abs(price-exit_bid)<1e-7)
     if depth>=p['qty']:
      fee=fee_est(p['qty'],exit_bid)
      pnl=round(p['qty']*(exit_bid-p['entry'])-p['entry_fee']-fee,2)
      db.execute("UPDATE trades SET exit=?,exit_fee=?,exit_time=?,pnl=?,exit_reason=?,state='CLOSED' WHERE id=? AND state='OPEN'",(exit_bid,fee,utcnow(),pnl,reason,p['id']))
      event(db,'EXIT',f'{ticker} {p["side"]} {reason} at {exit_bid*100:.1f}c P/L ${pnl:+.2f}')
  existing=db.execute('SELECT id FROM trades WHERE ticker=?',(ticker,)).fetchone()
  if not reasons and not existing and side_contract:
   fee=fee_est(QTY,ask);cost=round(QTY*ask+fee,2)
   if cash(db)>=cost:
    db.execute('INSERT INTO trades(ticker,side,qty,entry,entry_fee,entry_time) VALUES(?,?,?,?,?,?)',(ticker,side_contract,QTY,ask,fee,utcnow()))
    db.execute("UPDATE intervals SET decision='PAPER ENTRY' WHERE ticker=?",(ticker,))
    event(db,'ENTRY',f'{ticker} {side_contract} {QTY} @ {ask*100:.1f}c')
  db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('last_tick',utcnow()))
  db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('last_ticker',ticker))
  db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('last_signal',side))
  db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('last_error',''))
  db.commit()
 logging.info('%s %s %s/%s ADX %.1f %ss %s',ticker,side,u,d,adx,remaining,' | '.join(reasons) or 'QUALIFIED')

def loop():
 init()
 while True:
  try:tick()
  except Exception as exc:
   logging.exception('Monitor error')
   try:
    with connect() as db:
     db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('last_error',str(exc)[:500]))
     event(db,'ERROR',str(exc))
   except Exception:logging.exception('Failed to log error')
  time.sleep(POLL)

if __name__=='__main__':loop()
