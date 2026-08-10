import streamlit as st
import pandas as pd
import psycopg2
import os
from engine import PaperEngine

# ---------------- PAGE CONFIG ---------------- #
st.set_page_config(layout="wide")
st.title("📊 Trading Analytics Dashboard")

# ---------------- DB CONNECTION ---------------- #
@st.cache_resource
def get_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

conn = get_connection()

# ---------------- RUN SCANNER ---------------- #
st.subheader("⚡ Controls")

if st.button("Run Scanner Now"):
    try:
        st.info("⏳ Running scanner... please wait")

        engine = PaperEngine()
        engine.run_once()

        st.success("✅ Scanner executed successfully")

    except Exception as e:
        st.error(f"❌ Error running scanner: {e}")

# ---------------- LOAD DATA ---------------- #
try:
    df = pd.read_sql("SELECT * FROM trades ORDER BY created_at DESC", conn)
except Exception as e:
    st.error(f"❌ DB Error: {e}")
    st.stop()

# ---------------- EMPTY STATE ---------------- #
if df.empty:
    st.warning("No trades yet")
    st.stop()

# ---------------- METRICS ---------------- #
closed = df[df["status"] == "CLOSED"]

total_trades = len(df)
wins = len(closed[closed["pnl"] > 0])
losses = len(closed[closed["pnl"] <= 0])

win_rate = (wins / len(closed) * 100) if len(closed) else 0
total_pnl = closed["pnl"].sum() if not closed.empty else 0

col1, col2, col3 = st.columns(3)

col1.metric("Total Trades", total_trades)
col2.metric("Win Rate", f"{win_rate:.2f}%")
col3.metric("Total PnL", f"{total_pnl:.2f}")

# ---------------- EQUITY CURVE ---------------- #
st.subheader("📈 Equity Curve")

if not closed.empty:
    closed = closed.sort_values("exit_time")
    closed["equity"] = closed["pnl"].cumsum()

    st.line_chart(closed.set_index("exit_time")["equity"])
else:
    st.info("No closed trades yet")

# ---------------- TABLE ---------------- #
st.subheader("📋 Trades Data")
st.dataframe(df, use_container_width=True)
