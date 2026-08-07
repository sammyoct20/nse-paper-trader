import pandas as pd,numpy as np
def rsi(s,n=14):
 d=s.diff();u=d.clip(lower=0);dn=-d.clip(upper=0);rs=u.ewm(alpha=1/n,adjust=False).mean()/dn.ewm(alpha=1/n,adjust=False).mean().replace(0,np.nan);return 100-100/(1+rs)
def analyze(df,p):
 d=df.copy();d["s20"]=d.close.rolling(20).mean();d["s50"]=d.close.rolling(50).mean();d["s200"]=d.close.rolling(200).mean();d["rsi"]=rsi(d.close);d["v20"]=d.volume.rolling(20).mean();pr=d.close.shift(1);tr=pd.concat([d.high-d.low,(d.high-pr).abs(),(d.low-pr).abs()],axis=1).max(axis=1);d["atr"]=tr.rolling(14).mean();x=d.iloc[-1];atr=float(x.atr);p=float(p)
 if not np.isfinite(atr) or p<=x.s200 or x.s20<=x.s50 or x.rsi<48 or x.rsi>72 or atr/p>.055 or x.volume<.5*x.v20 or p>d.close.iloc[-2]*1.07:return None
 score=55;why=["trend structure"]
 if p>x.s20:score+=10
 if 52<=x.rsi<=68:score+=15;why.append("RSI trend zone")
 if x.volume>=1.2*x.v20:score+=10;why.append("volume support")
 br=p>d.high.tail(21).iloc[:-1].max()
 if br:score+=10;why.append("20-day breakout")
 risk=0 if atr/p<=.02 else 4 if atr/p<=.03 else 8 if atr/p<=.04 else 15
 stop=p-1.5*atr;target=p+2*(p-stop)
 return {"LTP":round(p,2),"Setup Score":min(score,100),"Risk Score":min(risk,40),"Setup":"Breakout" if br else "Trend continuation","Stop Loss":round(stop,2),"Target":round(target,2),"Why":"; ".join(why)}
