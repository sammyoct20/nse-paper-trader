import streamlit as st
import pandas as pd
import psycopg2
import os

st.title("📊 Trading Analytics Dashboard")

conn = psycopg2.connect(os.environ["DATABASE_URL"])

df = pd.read_sql("SELECT * FROM trades ORDER BY entry_time ASC", conn)

if df.empty:
    st.warning("No trades yet")
    st.stop()

st.subheader("All Trades")
st.dataframe(df)

closed = df[df["status"].isin(["TARGET HIT", "SL HIT"])].copy()

if closed.empty:
    st.warning("No closed trades yet")
    st.stop()

# ----- METRICS -----
total = len(closed)
wins = len(closed[closed["status"] == "TARGET HIT"])
losses = len(closed[closed["status"] == "SL HIT"])

win_rate = (wins / total) * 100 if total else 0
total_pnl = closed["pnl"].sum()

avg_win = closed[closed["pnl"] > 0]["pnl"].mean()
avg_loss = closed[closed["pnl"] < 0]["pnl"].mean()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Trades", total)
col2.metric("Win %", round(win_rate, 2))
col3.metric("Total PnL", round(total_pnl, 2))
col4.metric("Avg Win/Loss", f"{round(avg_win or 0,2)} / {round(avg_loss or 0,2)}")

# ----- EQUITY CURVE -----
closed["cum_pnl"] = closed["pnl"].cumsum()
st.subheader("Equity Curve")
st.line_chart(closed["cum_pnl"])

# ----- DRAWDOWN -----
closed["peak"] = closed["cum_pnl"].cummax()
closed["drawdown"] = closed["cum_pnl"] - closed["peak"]

st.subheader("Drawdown")
st.line_chart(closed["drawdown"])
st.metric("Max Drawdown", round(closed["drawdown"].min(), 2))

# ----- STREAKS -----
closed["result"] = closed["pnl"].apply(lambda x: "W" if x > 0 else "L")

max_win = max_loss = cur_win = cur_loss = 0

for r in closed["result"]:
    if r == "W":
        cur_win += 1
        cur_loss = 0
    else:
        cur_loss += 1
        cur_win = 0

    max_win = max(max_win, cur_win)
    max_loss = max(max_loss, cur_loss)

col1, col2 = st.columns(2)
col1.metric("Max Win Streak", max_win)
col2.metric("Max Loss Streak", max_loss)
