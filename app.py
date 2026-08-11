import streamlit as st
import pandas as pd
from engine import PaperEngine
from db import get_conn

st.set_page_config(page_title="Trading Dashboard", layout="wide")

st.title("📊 Swing Trading Dashboard")

engine = PaperEngine()

# ---------------- RUN BUTTON ----------------
if st.button("Run Scanner"):
    trades = engine.run()

    if trades:
        st.success(f"✅ {len(trades)} new trades added")
    else:
        st.warning("❌ No new trades (market weak or no setups)")

# ---------------- LOAD DATA ----------------
conn = get_conn()

open_df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
closed_df = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED'", conn)

conn.close()

# ---------------- TABS ----------------
tab1, tab2, tab3 = st.tabs(["🟢 Open Trades", "🔴 Closed Trades", "📈 Performance"])

# ---------------- OPEN TRADES ----------------
with tab1:
    st.subheader("🟢 Open Trades")

    if open_df.empty:
        st.info("No open trades")
    else:
        st.dataframe(open_df, use_container_width=True)

# ---------------- CLOSED TRADES ----------------
with tab2:
    st.subheader("🔴 Closed Trades")

    if closed_df.empty:
        st.info("No closed trades yet")
    else:
        # Show only important columns
        display_cols = [
            "symbol", "entry_price", "exit_price",
            "sl", "target", "pnl", "exit_reason", "closed_at"
        ]

        st.dataframe(closed_df[display_cols], use_container_width=True)

# ---------------- PERFORMANCE ----------------
with tab3:
    st.subheader("📈 Performance Summary")

    if closed_df.empty:
        st.info("No performance data yet")
    else:
        total_pnl = closed_df["pnl"].sum()
        wins = (closed_df["pnl"] > 0).sum()
        losses = (closed_df["pnl"] <= 0).sum()
        total_trades = len(closed_df)

        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0

        col1, col2, col3, col4 = st.columns(4)

        col1.metric("Total Trades", total_trades)
        col2.metric("Wins", wins)
        col3.metric("Losses", losses)
        col4.metric("Win Rate", f"{round(win_rate,2)}%")

        st.metric("Total PnL", round(total_pnl, 2))

        # Optional: PnL chart
        closed_df["cum_pnl"] = closed_df["pnl"].cumsum()
        st.line_chart(closed_df["cum_pnl"])
