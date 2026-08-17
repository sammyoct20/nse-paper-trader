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
    trades = engine.run()

    if trades:
        st.success(f"{len(trades)} new trades added")
    else:
        st.warning("No new trades")

# ---------------- LOAD DATA ----------------
conn = get_conn()

open_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
closed_df = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED'", conn)

conn.close()

# ---------------- CLEAN DATA ----------------
def clean_df(df):

    if df.empty:
        return df

    # Remove .NS (shorter names)
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.replace(".NS", "", regex=False)

    # Convert numeric safely
    cols = ["entry", "sl", "target", "entry_price", "exit_price", "pnl"]

    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df


open_df = clean_df(open_df)
closed_df = clean_df(closed_df)

# 🔥 THIS FIXES COLUMN TRUNCATION
pd.set_option("display.max_colwidth", None)

# ---------------- TABS ----------------
tab1, tab2, tab3 = st.tabs(["📂 Open Trades", "📁 Closed Trades", "📈 Performance"])

# ---------------- OPEN TRADES ----------------
with tab1:
    if open_df.empty:
        st.info("No open trades")
    else:
        st.dataframe(
            open_df,
            use_container_width=True,
            height=500
        )

# ---------------- CLOSED TRADES ----------------
with tab2:
    if closed_df.empty:
        st.info("No closed trades")
    else:
        st.dataframe(
            closed_df,
            use_container_width=True,
            height=500
        )

# ---------------- PERFORMANCE ----------------
with tab3:
    if closed_df.empty:
        st.info("No data yet")
    else:
        total_pnl = closed_df["pnl"].sum()
        win_rate = (closed_df["pnl"] > 0).mean() * 100

        col1, col2 = st.columns(2)
        col1.metric("Total PnL", round(total_pnl, 2))
        col2.metric("Win Rate", f"{round(win_rate,2)}%")

        closed_df["cum_pnl"] = closed_df["pnl"].cumsum()
        st.line_chart(closed_df["cum_pnl"])
