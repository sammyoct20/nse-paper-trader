import streamlit as st
import pandas as pd
from engine import PaperEngine
from db import get_conn, create_tables

# ---------------- INIT ----------------
create_tables()
engine = PaperEngine()

st.set_page_config(layout="wide")

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

    # Remove .NS for mobile readability
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].str.replace(".NS", "")

    # Round numeric values
    cols = ["entry", "sl", "target", "entry_price", "exit_price", "pnl"]
    for col in cols:
        if col in df.columns:
            df[col] = df[col].round(2)

    return df


open_df = clean_df(open_df)
closed_df = clean_df(closed_df)

# ---------------- CARD UI ----------------
def trade_card(trade, is_closed=False):

    if is_closed:
        color = "green" if trade["pnl"] > 0 else "red"
    else:
        color = "blue"

    st.markdown(f"""
    <div style="
        padding:15px;
        border-radius:12px;
        background-color:#1e1e1e;
        margin-bottom:10px;
        border-left:6px solid {color};
    ">
        <h4>{trade['symbol']}</h4>
        <p>Entry: {trade['entry_price']}</p>
        <p>SL: {trade['sl']}</p>
        <p>Target: {trade['target']}</p>
        {"<p>Exit: " + str(trade['exit_price']) + "</p>" if is_closed else ""}
        {"<p style='color:" + color + "'>PnL: " + str(trade['pnl']) + "</p>" if is_closed else ""}
        {"<p>Reason: " + str(trade['exit_reason']) + "</p>" if is_closed else ""}
    </div>
    """, unsafe_allow_html=True)


# ---------------- TABS ----------------
tab1, tab2, tab3 = st.tabs(["📂 Open Trades", "📁 Closed Trades", "📈 Performance"])

# ---------------- OPEN TRADES ----------------
with tab1:
    if open_df.empty:
        st.info("No open trades")
    else:
        for _, row in open_df.iterrows():
            trade_card(row)

# ---------------- CLOSED TRADES ----------------
with tab2:
    if closed_df.empty:
        st.info("No closed trades")
    else:
        for _, row in closed_df.iterrows():
            trade_card(row, is_closed=True)

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
