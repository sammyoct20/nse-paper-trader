import streamlit as st
import pandas as pd
from db import get_conn, create_tables
from engine import PaperEngine

st.set_page_config(layout="wide")

create_tables()
engine = PaperEngine()

st.title("⚡ Sammy - Multi-Asset Trading Engine")

# ---------------- RUN SCANNER ----------------
if st.button("🚀 Run Equity Market Scan"):
    engine.run()
    st.success("Scanner executed")

# ---------------- LOAD DB ----------------
conn = get_conn()
df = pd.read_sql("SELECT * FROM trades", conn)
conn.close()

# ---------------- CLEAN DATA ----------------
def clean(df):
    if df.empty:
        return df

    df["symbol"] = df["symbol"].astype(str).str.replace(".NS","",regex=False)

    # normalize type
    if "type" in df.columns:
        df["type"] = df["type"].str.upper()

    for col in ["entry","sl","target","entry_price","exit_price","pnl"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df

df = clean(df)

# ---------------- FILTER ----------------
open_df = df[df["status"]=="OPEN"]
closed_df = df[df["status"]=="CLOSED"]

intraday_df = open_df[open_df["type"]=="INTRADAY"]
swing_df = open_df[open_df["type"]=="SWING"]

# ---------------- TABS ----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📈 Swing Trades", "⚡ Intraday Trades", "📁 Closed Trades", "📊 Performance", "🔍 Analyzer"]
)

# ---------------- SWING ----------------
with tab1:
    st.subheader("Active Swing Trades")

    if swing_df.empty:
        st.warning("No active swing trades")
    else:
        st.dataframe(swing_df, use_container_width=True)

# ---------------- INTRADAY ----------------
with tab2:
    st.subheader("Active Intraday Trades")

    if intraday_df.empty:
        st.warning("No active intraday trades")
    else:
        st.dataframe(intraday_df, use_container_width=True)

# ---------------- CLOSED ----------------
with tab3:
    st.subheader("Closed Trades")

    if closed_df.empty:
        st.warning("No closed trades yet")
    else:
        st.dataframe(closed_df, use_container_width=True)

# ---------------- PERFORMANCE ----------------
with tab4:
    st.subheader("Performance")

    if not closed_df.empty:
        total_pnl = closed_df["pnl"].sum()
        win_rate = (closed_df["pnl"] > 0).mean() * 100

        col1, col2 = st.columns(2)

        col1.metric("Total PnL", f"₹{round(total_pnl,2)}")
        col2.metric("Win Rate", f"{round(win_rate,2)}%")
    else:
        st.info("No performance data yet")

# ---------------- ANALYZER ----------------
with tab5:
    st.subheader("Stock Analyzer")

    symbol = st.text_input("Enter Stock (e.g. RELIANCE, TCS)")

    if st.button("Analyze"):
        result = engine.analyze_stock(symbol)

        if "error" in result:
            st.error(result["error"])
        else:
            st.json(result)

# ---------------- DEBUG (optional) ----------------
with st.expander("🔍 Debug - All Trades"):
    st.dataframe(df)
