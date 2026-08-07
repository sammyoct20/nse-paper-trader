import requests,pandas as pd
from datetime import datetime,timedelta
from strategy import analyze
class NSEClient:
 BASE="https://www.nseindia.com"
 def __init__(self):
  self.s=requests.Session();self.s.headers.update({"User-Agent":"Mozilla/5.0 Chrome/126 Safari/537.36","Accept":"application/json,text/plain,*/*","Referer":"https://www.nseindia.com/"})
  try:self.s.get(self.BASE,timeout=15)
  except:pass
 def get(self,path,params=None):r=self.s.get(self.BASE+path,params=params,timeout=20);r.raise_for_status();return r.json()
 def symbols(self):return "RELIANCE TCS HDFCBANK ICICIBANK BHARTIARTL INFY SBIN HINDUNILVR ITC LT KOTAKBANK AXISBANK MARUTI M&M SUNPHARMA TATASTEEL TATAMOTORS TITAN BAJFINANCE ADANIENT ADANIPORTS NTPC POWERGRID ONGC WIPRO HCLTECH ULTRACEMCO ASIANPAINT BAJAJFINSV JSWSTEEL COALINDIA TRENT BEL HAL EICHERMOT SHRIRAMFIN GRASIM TECHM TATACONSUM DRREDDY CIPLA APOLLOHOSP DIVISLAB SBILIFE HDFCLIFE DLF PIDILITIND SIEMENS VBL DMART INDUSINDBK HINDALCO BPCL IOC VEDL JINDALSTEL AMBUJACEM DABUR BRITANNIA HEROMOTOCO BAJAJ-AUTO TVSMOTOR MOTHERSON BOSCH POLYCAB PERSISTENT LTIM MPHASIS COFORGE MAXHEALTH LUPIN AUROPHARMA TORNTPHARM ZYDUSLIFE ICICIPRULI SBICARD ICICIGI HAVELLS VOLTAS CUMMINS ABB".split()
 def history(self,s):
  e=datetime.now();a=e-timedelta(days=380);j=self.get("/api/historical/cm/equity",{"symbol":s,"from":a.strftime("%d-%m-%Y"),"to":e.strftime("%d-%m-%Y")});d=pd.DataFrame(j.get("data",[]))
  if d.empty:return d
  d=d.rename(columns={"CH_TIMESTAMP":"date","CH_OPENING_PRICE":"open","CH_TRADE_HIGH_PRICE":"high","CH_TRADE_LOW_PRICE":"low","CH_CLOSING_PRICE":"close","CH_TOT_TRADED_QTY":"volume"})
  for x in ["open","high","low","close","volume"]:d[x]=pd.to_numeric(d[x],errors="coerce")
  return d.dropna(subset=["close"]).sort_values("date")
 def quote(self,s):return float(self.get("/api/quote-equity",{"symbol":s})["priceInfo"]["lastPrice"])
 def scan(self):
  a=[]
  for s in self.symbols():
   try:
    h=self.history(s)
    if len(h)>=220:
     r=analyze(h,self.quote(s))
     if r:r["Symbol"]=s;a.append(r)
   except:pass
  return pd.DataFrame(a)
