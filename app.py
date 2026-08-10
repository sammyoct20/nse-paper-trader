import streamlit as st
import pandas as pd
import psycopg2
import os

st.set_page_config(layout="wide")

st.title("📊 Trading Dashboard")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))

df = pd.read_sql("SELECT * FROM trades ORDER BY created_at ASC", conn)

if df.empty:
    st.warning("No trades yet")
    st.stop()

closed = df[df["status"] == "CLOSED"]

total = len(df)
wins = len(closed[closed["pnl"] > 0])
loss = len(closed[closed["pnl"] <= 0])

win_rate = (wins / len(closed) * 100) if len(closed) else 0
total_pnl = closed["pnl"].sum()

col1, col2, col3 = st.columns(3)

col1.metric("Total Trades", total)
col2.metric("Win Rate", f"{win_rate:.2f}%")
col3.metric("PnL", f"{total_pnl:.2f}")

closed = closed.sort_values("exit_time")
closed["equity"] = closed["pnl"].cumsum()

st.subheader("Equity Curve")
st.line_chart(closed.set_index("exit_time")["equity"])

st.subheader("All Trades")
st.dataframe(df, use_container_width=True)
