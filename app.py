import streamlit as st
import pandas as pd
import psycopg2
import os
from datetime import datetime
from engine import PaperEngine

# ---------------- PAGE CONFIG ---------------- #
st.set_page_config(page_title="Trading Dashboard", layout="wide")

st.title("📊 Trading Dashboard")

# ---------------- DB CONNECTION ---------------- #
@st.cache_resource
def get_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

conn = get_connection()

# ---------------- ENGINE ---------------- #
engine = PaperEngine()

# ---------------- HEADER ---------------- #
col1, col2 = st.columns([3, 1])

with col1:
    st.write("Smart Swing Trading System")

with col2:
    if "last_run" in st.session_state:
        st.caption(f"Last run: {st.session_state['last_run']}")
    else:
        st.caption("Last run: Never")

# ---------------- RUN SCANNER ---------------- #
if st.button("🚀 Run Scanner Now"):

    with st.spinner("Scanning market... please wait"):
        signals = engine.scan_market()
        trades = engine.generate_trades(signals)
        engine.save_trades(trades)
        engine.update_trades()

        st.session_state["last_run"] = datetime.now().strftime("%d-%b %H:%M:%S")

    st.success(f"Scan complete | Signals: {len(signals)} | Trades: {len(trades)}")

# ---------------- LOAD DATA ---------------- #
def load_trades():
    try:
        df = pd.read_sql("""
            SELECT * FROM trades 
            ORDER BY created_at DESC
        """, conn)
        return df
    except:
        return pd.DataFrame()

df = load_trades()

# ---------------- TABS ---------------- #
tab1, tab2, tab3 = st.tabs(["📈 Open Trades", "📊 Closed Trades", "📉 Analytics"])

# ---------------- OPEN TRADES ---------------- #
with tab1:

    st.subheader("Open Trades")

    if df.empty:
        st.info("No trades yet")
    else:
        open_df = df[df["status"] == "OPEN"]

        if open_df.empty:
            st.info("No open trades")
        else:
            st.dataframe(open_df, use_container_width=True)

# ---------------- CLOSED TRADES ---------------- #
with tab2:

    st.subheader("Closed Trades")

    if df.empty:
        st.info("No trades yet")
    else:
        closed_df = df[df["status"] == "CLOSED"]

        if closed_df.empty:
            st.info("No closed trades yet")
        else:
            st.dataframe(closed_df, use_container_width=True)

# ---------------- ANALYTICS ---------------- #
with tab3:

    st.subheader("Performance Analytics")

    if df.empty:
        st.info("No data yet")
    else:
        closed_df = df[df["status"] == "CLOSED"]

        if closed_df.empty:
            st.warning("No closed trades to analyze")
        else:
            total_trades = len(closed_df)
            wins = len(closed_df[closed_df["pnl"] > 0])
            losses = len(closed_df[closed_df["pnl"] <= 0])

            win_rate = round((wins / total_trades) * 100, 2)
            total_pnl = closed_df["pnl"].sum()

            col1, col2, col3 = st.columns(3)

            col1.metric("Total Trades", total_trades)
            col2.metric("Win Rate", f"{win_rate}%")
            col3.metric("Total PnL", round(total_pnl, 2))

            # Equity Curve
            closed_df = closed_df.sort_values("exit_time")
            closed_df["equity"] = closed_df["pnl"].cumsum()

            st.line_chart(closed_df["equity"])
