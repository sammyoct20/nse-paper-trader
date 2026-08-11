import streamlit as st
import pandas as pd
import psycopg2
import os
from engine import PaperEngine

st.set_page_config(page_title="Trading Dashboard", layout="wide")

st.title("📊 Trading Dashboard")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))

# ---------------------------
# RUN SCANNER
# ---------------------------
if st.button("🚀 Run Scanner Now"):
    with st.spinner("Running scanner..."):
        engine = PaperEngine()
        signals, trades = engine.run_once()

    st.success(f"Done | Signals: {signals} | Trades: {trades}")

# ---------------------------
# LOAD DATA
# ---------------------------
df = pd.read_sql("SELECT * FROM trades ORDER BY created_at DESC", conn)

# ---------------------------
# TABS
# ---------------------------
tab1, tab2, tab3 = st.tabs(["Open Trades", "Closed Trades", "Analytics"])

# ---------------------------
# OPEN TRADES
# ---------------------------
with tab1:
    st.subheader("Open Trades")
    st.dataframe(df[df["status"] == "OPEN"])

# ---------------------------
# CLOSED TRADES
# ---------------------------
with tab2:
    st.subheader("Closed Trades")
    st.dataframe(df[df["status"] == "CLOSED"])

# ---------------------------
# ANALYTICS
# ---------------------------
with tab3:
    st.subheader("Performance")

    if len(df) == 0:
        st.info("No trades yet")
    else:
        df["cum_pnl"] = df["pnl"].fillna(0).cumsum()
        st.line_chart(df["cum_pnl"])
        st.write("Total PnL:", df["pnl"].sum())
