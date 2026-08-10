import streamlit as st
import pandas as pd
import psycopg2
import os

st.title("📊 Trading Dashboard")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))

df = pd.read_sql("SELECT * FROM trades ORDER BY created_at ASC", conn)

if df.empty:
    st.warning("No trades yet")
    st.stop()

# Metrics
closed = df[df["status"] == "CLOSED"]

total = len(df)
wins = len(closed[closed["pnl"] > 0])
loss = len(closed[closed["pnl"] <= 0])

win_rate = (wins / len(closed) * 100) if len(closed) else 0
total_pnl = closed["pnl"].sum()

st.metric("Total Trades", total)
st.metric("Win Rate", f"{win_rate:.2f}%")
st.metric("PnL", f"{total_pnl:.2f}")

# Equity Curve
closed = closed.sort_values("exit_time")
closed["equity"] = closed["pnl"].cumsum()

st.line_chart(closed.set_index("exit_time")["equity"])

st.dataframe(df)
