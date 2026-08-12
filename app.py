import streamlit as st
import pandas as pd
from engine import PaperEngine
from db import get_conn, create_tables

create_tables()

st.title("📊 Trading Dashboard")

engine = PaperEngine()

if st.button("Run Scanner"):
    trades = engine.run()

    if trades:
        st.success(f"{len(trades)} new trades added")
    else:
        st.warning("No new trades")

conn = get_conn()

open_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
closed_df = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED'", conn)

conn.close()

tab1, tab2, tab3 = st.tabs(["Open Trades", "Closed Trades", "Performance"])

with tab1:
    st.dataframe(open_df)

with tab2:
    st.dataframe(closed_df[[
        "symbol","entry_price","exit_price",
        "sl","target","pnl","exit_reason","closed_at"
    ]])

with tab3:
    if not closed_df.empty:
        total_pnl = closed_df["pnl"].sum()
        win_rate = (closed_df["pnl"] > 0).mean() * 100

        st.metric("Total PnL", round(total_pnl, 2))
        st.metric("Win Rate", f"{round(win_rate,2)}%")

        closed_df["cum_pnl"] = closed_df["pnl"].cumsum()
        st.line_chart(closed_df["cum_pnl"])
