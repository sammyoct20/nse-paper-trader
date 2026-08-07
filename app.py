import streamlit as st,pandas as pd
from engine import PaperEngine
st.set_page_config(page_title="NSE Paper Trader",page_icon="📈",layout="wide")
st.title("📈 NSE Paper Trader — Cloud V6")
st.caption("Paper trading only • no broker orders")
e=PaperEngine();cfg=e.config()
with st.sidebar:
    st.header("Paper account")
    capital=st.number_input("Starting capital (₹)",10000,100000000,cfg["capital"],10000)
    risk=st.number_input("Risk per trade (%)",.1,2.,cfg["risk_pct"],.1)
    maxpos=st.number_input("Max positions",1,20,cfg["max_pos"])
    score=st.slider("Minimum setup score",60,100,cfg["min_score"])
    mrisk=st.slider("Maximum risk score",0,40,cfg["max_risk"])
    slip=st.number_input("Slippage (bps)",0,100,cfg["slippage_bps"])
    if st.button("Save settings"):
        e.save_config(capital,risk,maxpos,score,mrisk,slip);st.success("Saved")
m=e.metrics(capital)
for col,label,val in zip(st.columns(6),["Closed","Open","Win rate","Net P/L","Profit factor","Max drawdown"],
 [m["closed"],m["open"],f'{m["win_rate"]:.1f}%',f'₹{m["net"]:,.0f}',f'{m["pf"]:.2f}' if m["pf"]!=float("inf") else "∞",f'₹{m["dd"]:,.0f}']): col.metric(label,val)
st.info(f'Last scan: {e.last_run()} • Status: {e.last_status()}')
o,c,s=e.tables()
st.subheader("🟢 Open positions");st.dataframe(o,use_container_width=True,hide_index=True) if not o.empty else st.info("No open positions.")
st.subheader("📕 Closed trades");st.dataframe(c,use_container_width=True,hide_index=True) if not c.empty else st.info("No closed trades yet.")
st.subheader("🔎 Recent qualifying signals");st.dataframe(s,use_container_width=True,hide_index=True) if not s.empty else st.info("No qualifying signals.")
