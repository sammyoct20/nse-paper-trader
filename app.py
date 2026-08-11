import streamlit as st
import pandas as pd
from engine import PaperEngine
from db import get_conn

st.title("📊 Trading Dashboard")

engine = PaperEngine()

if st.button("Run Scanner"):
    trades = engine.run()

    if trades:
        st.success(f"{len(trades)} new trades added")
    else:
        st.warning("No new trades")

# -------- OPEN TRADES --------
st.subheader("Open Trades")

conn = get_conn()
open_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)

st.dataframe(open_df)

# -------- CLOSED TRADES --------
st.subheader("Closed Trades")

closed_df = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED'", conn)

st.dataframe(closed_df)

# -------- PNL --------
st.subheader("Performance")

if not closed_df.empty:
    total_pnl = closed_df["pnl"].sum()
    win_rate = (closed_df["pnl"] > 0).mean() * 100

    st.metric("Total PnL", round(total_pnl, 2))
    st.metric("Win Rate", f"{round(win_rate,2)}%")

conn.close()
