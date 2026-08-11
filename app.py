import streamlit as st
import pandas as pd
import psycopg2
import os
from datetime import datetime
from engine import PaperEngine

st.set_page_config(layout="wide")
st.title("📊 Trading Dashboard")

@st.cache_resource
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

conn = get_conn()
engine = PaperEngine()

if "last_run" not in st.session_state:
    st.session_state["last_run"] = "Never"

st.write(f"Last Run: {st.session_state['last_run']}")

if st.button("🚀 Run Scanner Now"):

    with st.spinner("Scanning market..."):
        signals = engine.scan_market()
        trades = engine.generate_trades(signals)

        engine.save_trades(trades)
        engine.update_trades()

        st.session_state["last_run"] = datetime.now().strftime("%H:%M:%S")

    st.success(f"Done | Signals: {len(signals)} | Trades: {len(trades)}")

df = pd.read_sql("SELECT * FROM trades ORDER BY created_at DESC", conn)

tab1, tab2, tab3 = st.tabs(["Open Trades", "Closed Trades", "Analytics"])

with tab1:
    open_df = df[df["status"] == "OPEN"]
    st.dataframe(open_df if not open_df.empty else pd.DataFrame())

with tab2:
    closed_df = df[df["status"] == "CLOSED"]
    st.dataframe(closed_df if not closed_df.empty else pd.DataFrame())

with tab3:
    closed_df = df[df["status"] == "CLOSED"]

    if not closed_df.empty:
        total = len(closed_df)
        wins = len(closed_df[closed_df["pnl"] > 0])
        win_rate = round((wins / total) * 100, 2)
        pnl = closed_df["pnl"].sum()

        st.metric("Total Trades", total)
        st.metric("Win Rate", f"{win_rate}%")
        st.metric("PnL", round(pnl, 2))

        closed_df = closed_df.sort_values("exit_time")
        closed_df["equity"] = closed_df["pnl"].cumsum()

        st.line_chart(closed_df["equity"])
    else:
        st.info("No closed trades yet")
