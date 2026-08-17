import streamlit as st
import pandas as pd
from engine import PaperEngine
from db import get_conn, create_tables

# ---------------- INIT ----------------
st.set_page_config(layout="wide")
create_tables()
engine = PaperEngine()

st.title("📊 Trading Dashboard")

# ---------------- RUN SCANNER ----------------
if st.button("🚀 Run Scanner"):
    engine.run()
    st.success("Scanner executed")

# ---------------- LOAD DATA ----------------
conn = get_conn()

df = pd.read_sql("SELECT * FROM trades", conn)
conn.close()

# ---------------- CLEAN DATA ----------------
def clean_df(df):

    if df.empty:
        return df

    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.replace(".NS", "", regex=False)

    cols = ["entry", "sl", "target", "entry_price", "exit_price", "pnl"]

    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df


df = clean_df(df)

# ---------------- FILTER DATA ----------------
open_df = df[df["status"] == "OPEN"]
closed_df = df[df["status"] == "CLOSED"]

intraday_df = open_df[open_df["type"] == "INTRADAY"]
swing_df = open_df[open_df["type"] == "SWING"]

# ---------------- UI TABS ----------------
tab1, tab2, tab3, tab4 = st.tabs([
    "⚡ Intraday",
    "📈 Swing",
    "📁 Closed Trades",
    "📊 Performance"
])

# ---------------- INTRADAY TAB ----------------
with tab1:
    if intraday_df.empty:
        st.warning("No intraday trades")
    else:
        st.dataframe(intraday_df, use_container_width=True)

# ---------------- SWING TAB ----------------
with tab2:
    if swing_df.empty:
        st.warning("No swing trades")
    else:
        st.dataframe(swing_df, use_container_width=True)

# ---------------- CLOSED TRADES ----------------
with tab3:
    if closed_df.empty:
        st.warning("No closed trades")
    else:
        st.dataframe(closed_df, use_container_width=True)

# ---------------- PERFORMANCE ----------------
with tab4:
    if closed_df.empty:
        st.info("No data yet")
    else:
        total_pnl = closed_df["pnl"].sum()
        win_rate = (closed_df["pnl"] > 0).mean() * 100

        col1, col2 = st.columns(2)
        col1.metric("Total PnL", round(total_pnl, 2))
        col2.metric("Win Rate", f"{round(win_rate, 2)}%")

        closed_df["cum_pnl"] = closed_df["pnl"].cumsum()
        st.line_chart(closed_df["cum_pnl"])
