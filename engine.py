import os,psycopg2,pandas as pd
from datetime import datetime,timezone
from nse_client import NSEClient
CFG={"capital":500000,"risk_pct":.5,"max_pos":5,"min_score":78,"max_risk":18,"slippage_bps":10}
class PaperEngine:
 def __init__(self): self.client=NSEClient();self.db=os.environ["DATABASE_URL"];self.init()
 def con(self): return psycopg2.connect(self.db,sslmode="require")
 def init(self):
  c=self.con();q=c.cursor();q.execute("""CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
  CREATE TABLE IF NOT EXISTS trades(id SERIAL PRIMARY KEY,symbol TEXT,setup TEXT,entry FLOAT,current FLOAT,stop FLOAT,target FLOAT,qty INT,risk FLOAT,opened TIMESTAMPTZ,exit FLOAT,exit_reason TEXT,closed TIMESTAMPTZ,costs FLOAT,pnl FLOAT,status TEXT);
  CREATE TABLE IF NOT EXISTS signals(id SERIAL PRIMARY KEY,time TIMESTAMPTZ,symbol TEXT,ltp FLOAT,setup_score INT,risk_score INT,setup TEXT,entry FLOAT,stop FLOAT,target FLOAT,why TEXT);
  CREATE TABLE IF NOT EXISTS runs(id SERIAL PRIMARY KEY,time TIMESTAMPTZ,status TEXT,message TEXT);""");c.commit();q.close();c.close()
 def config(self):
  c=self.con();q=c.cursor();q.execute("SELECT k,v FROM settings");x=CFG.copy()
  for k,v in q.fetchall():
   try:x[k]=float(v) if k in ("capital","risk_pct","slippage_bps") else int(float(v))
   except:pass
  q.close();c.close();return x
 def save_config(self,*a):
  vals=dict(zip(["capital","risk_pct","max_pos","min_score","max_risk","slippage_bps"],a));c=self.con();q=c.cursor()
  for k,v in vals.items():q.execute("INSERT INTO settings VALUES(%s,%s) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v",(k,str(v)))
  c.commit();q.close();c.close()
 def run_once(self):
  cfg=self.config();now=datetime.now(timezone.utc)
  try:
   d=self.client.scan();c=self.con();q=c.cursor()
   q.execute("SELECT id,symbol,entry,stop,target,qty FROM trades WHERE status='OPEN'")
   for tid,sym,entry,stop,target,qty in q.fetchall():
    z=d[d.Symbol==sym]
    if z.empty:continue
    p=float(z.iloc[0].LTP)
    if p<=stop:self.close(q,tid,entry,stop,qty,"STOP LOSS",now)
    elif p>=target:self.close(q,tid,entry,target,qty,"TARGET",now)
    else:q.execute("UPDATE trades SET current=%s WHERE id=%s",(p,tid))
   q.execute("SELECT symbol FROM trades WHERE status='OPEN'");occ={r[0] for r in q.fetchall()}
   cand=d[(d["Setup Score"]>=cfg["min_score"])&(d["Risk Score"]<=cfg["max_risk"])] if not d.empty else d
   cand=cand[~cand.Symbol.isin(occ)].sort_values(["Risk Score","Setup Score"])
   for _,r in cand.head(max(0,int(cfg["max_pos"])-len(occ))).iterrows():
    raw=float(r.LTP);entry=raw*(1+cfg["slippage_bps"]/10000);stop=float(r["Stop Loss"]);target=float(r["Target"]);qty=int(cfg["capital"]*cfg["risk_pct"]/100/max(entry-stop,.01))
    if qty<=0:continue
    q.execute("INSERT INTO trades(symbol,setup,entry,current,stop,target,qty,risk,opened,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN')",(r.Symbol,r.Setup,entry,entry,stop,target,qty,qty*(entry-stop),now))
    q.execute("INSERT INTO signals(time,symbol,ltp,setup_score,risk_score,setup,entry,stop,target,why) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(now,r.Symbol,raw,int(r["Setup Score"]),int(r["Risk Score"]),r.Setup,entry,stop,target,r.Why))
   q.execute("INSERT INTO runs(time,status,message) VALUES(%s,'OK',%s)",(now,f"{len(d)} stocks scanned"));c.commit();q.close();c.close()
  except Exception as ex:
   c=self.con();c.cursor().execute("INSERT INTO runs(time,status,message) VALUES(%s,'ERROR',%s)",(now,str(ex)));c.commit();c.close()
 def close(self,q,tid,entry,exitp,qty,reason,now):
  gross=(exitp-entry)*qty;cost=max(20,(entry+exitp)*qty*.00025);q.execute("UPDATE trades SET exit=%s,exit_reason=%s,closed=%s,costs=%s,pnl=%s,status='CLOSED',current=%s WHERE id=%s",(exitp,reason,now,cost,gross-cost,exitp,tid))
 def last_run(self):
  c=self.con();q=c.cursor();q.execute("SELECT time FROM runs ORDER BY id DESC LIMIT 1");r=q.fetchone();q.close();c.close();return r[0].strftime("%d-%b-%Y %H:%M UTC") if r else "Never"
 def last_status(self):
  c=self.con();q=c.cursor();q.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1");r=q.fetchone();q.close();c.close();return r[0] if r else "NOT STARTED"
 def metrics(self,capital):
  c=self.con();p=pd.read_sql("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY id",c);o=pd.read_sql("SELECT id FROM trades WHERE status='OPEN'",c);c.close()
  if p.empty:return {"closed":0,"open":len(o),"win_rate":0,"net":0,"pf":0,"dd":0}
  p=p.pnl.astype(float);eq=capital+p.cumsum();wins=p[p>0].sum();loss=-p[p<0].sum()
  return {"closed":len(p),"open":len(o),"win_rate":float((p>0).mean()*100),"net":float(p.sum()),"pf":float(wins/loss) if loss else float("inf"),"dd":float((eq-eq.cummax()).min())}
 def tables(self):
  c=self.con();o=pd.read_sql("SELECT symbol Symbol,setup Setup,entry Entry,current Current,stop \"Stop Loss\",target Target,qty Qty,risk \"Risk ₹\",opened Opened,(current-entry)*qty \"Unrealized P/L\" FROM trades WHERE status='OPEN'",c);t=pd.read_sql("SELECT symbol Symbol,setup Setup,entry Entry,exit Exit,qty Qty,stop \"Stop Loss\",target Target,exit_reason \"Exit reason\",opened Opened,closed Closed,costs Costs,pnl \"P/L\" FROM trades WHERE status='CLOSED' ORDER BY id DESC",c);s=pd.read_sql("SELECT time Time,symbol Symbol,ltp LTP,setup_score \"Setup Score\",risk_score \"Risk Score\",setup Setup,entry Entry,stop \"Stop Loss\",target Target,why Why FROM signals ORDER BY id DESC LIMIT 50",c);c.close();return o,t,s
