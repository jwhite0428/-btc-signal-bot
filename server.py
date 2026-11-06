"""Read-only JSON dashboard API + background worker, one Render web instance."""
import os, threading, sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from worker import init, loop, connect, cash

@asynccontextmanager
async def lifespan(app):
 init()
 thread=threading.Thread(target=loop,name='sentinel-paper-worker',daemon=True)
 thread.start()
 yield

app=FastAPI(title='SENTINEL 24/7 paper worker',lifespan=lifespan)

def auth(authorization):
 secret=os.environ.get('DASHBOARD_TOKEN','')
 if not secret or authorization!=f'Bearer {secret}':
  raise HTTPException(401,'Valid dashboard bearer token required')

def records(db,sql,args=()):return [dict(row) for row in db.execute(sql,args).fetchall()]

@app.get('/health')
def health():
 # Liveness only, not proof of fresh market data.
 return {'service':'sentinel-paper-worker','running':True,'paper_only':True}

@app.get('/api/snapshot')
def snapshot(authorization:str|None=Header(default=None)):
 auth(authorization)
 with connect() as db:
  meta={r['key']:r['value'] for r in db.execute('SELECT key,value FROM meta')}
  trades=records(db,'SELECT * FROM trades ORDER BY id DESC LIMIT 500')
  intervals=records(db,'SELECT * FROM intervals ORDER BY first_seen DESC LIMIT 500')
  events=records(db,'SELECT * FROM events ORDER BY id DESC LIMIT 100')
  closed=[t for t in trades if t['state']=='CLOSED']
  wins=sum(t['pnl']>0 for t in closed);losses=sum(t['pnl']<0 for t in closed)
  pnl=round(sum(t['pnl'] for t in closed),2)
  return {'meta':meta,'paper_cash':cash(db),'open_positions':[t for t in trades if t['state']=='OPEN'],'trades':trades,'intervals':intervals,'events':events,'score':{'wins':wins,'losses':losses,'win_rate_pct':round(100*wins/(wins+losses),1) if wins+losses else None,'net_pnl':pnl},'paper_only':True}
