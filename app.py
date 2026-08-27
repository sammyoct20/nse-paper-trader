import os
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
from engine import PaperEngine

st.set_page_config(page_title="NSE Automated Algo & Strategy Dashboard", layout="wide")
st.title("⚡ NSE Automated Trading Engine")

# Database Connection
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///paper_trading.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

db_engine = create_engine(DATABASE_URL)

if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

@st.cache_data(ttl=900)
def fetch_scan_results(index_name):
    universe = st.session_state.engine.fetch_nse_universe(index_name)
    return st.session_state.engine.scan_all_strategies(universe)

st.sidebar.header("Scan Parameters")
selected_index = st.sidebar.selectbox("Market Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)

if st.sidebar.button("🚀 Run Live Market Scan"):
    with st.spinner(f"Scanning {selected_index}..."):
        st.session_state["scan_results"] = fetch_scan_results(selected_index)
    st.sidebar.success("Scan Completed!")

# App Tabs
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🤖 Auto Portfolio", "📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer", "🧪 Backtester"
])

# TAB 1: AUTO PORTFOLIO
with tab1:
    st.subheader("Live Automated Paper Trading Positions")
    try:
        df_trades = pd.read_sql("SELECT * FROM trades ORDER BY id DESC", db_engine)
    except Exception:
        df_trades = pd.DataFrame()

    if not df_trades.empty:
        open_df = df_trades[df_trades["status"] == "OPEN"]
        closed_df = df_trades[df_trades["status"] != "OPEN"]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Active Positions", len(open_df))
        c2.metric("Closed Trades", len(closed_df))
        
        st.markdown("### 🟢 Active Open Positions")
        st.dataframe(open_df, use_container_width=True)
        
        st.markdown("### 📜 Executed Trades History")
        st.dataframe(closed_df, use_container_width=True)
    else:
        st.info("No trades recorded yet. The automated runner will populate positions at 3:15 PM.")

# TAB 2: SWING
with tab2:
    st.subheader("Swing Opportunities")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["SWING"], use_container_width=True)
    else:
        st.info("Click 'Run Live Market Scan' in sidebar.")

# TAB 3: INTRADAY
with tab3:
    st.subheader("Intraday Momentum Setups")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["INTRADAY"], use_container_width=True)
    else:
        st.info("Click 'Run Live Market Scan' in sidebar.")

# TAB 4: BTST
with tab4:
    st.subheader("BTST Candidates")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["BTST"], use_container_width=True)
    else:
        st.info("Click 'Run Live Market Scan' in sidebar.")

# TAB 5: ANALYZER
with tab5:
    st.subheader("Stock Technical Diagnostic")
    symbol_input = st.text_input("NSE Ticker:", "RELIANCE")
    if st.button("Analyze Stock"):
        res = st.session_state.engine.analyze_stock(symbol_input)
        if "Error" in res:
            st.error(res["Error"])
        else:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Score", f"{res['Score']} / 100")
            col2.metric("Price", f"₹{res['Price']}")
            col3.metric("Stop Loss", f"₹{res['StopLoss']}")
            col4.metric("Target", f"₹{res['Target']}")
            for r in res["Reasons"]:
                st.write(r)

# TAB 6: BACKTESTER
with tab6:
    st.subheader("Automated Historical Strategy Backtester")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        bt_symbol = st.text_input("Ticker Symbol:", "TATAMOTORS")
    with col_b:
        bt_strategy = st.selectbox("Strategy:", ["SWING", "BTST"])
    with col_c:
        bt_days = st.slider("Window (Days):", 90, 730, 365)
        
    if st.button("Run Automated Backtest"):
        with st.spinner("Simulating historical trades..."):
            res = st.session_state.engine.backtest_strategy(bt_symbol, bt_strategy, bt_days)
            if "Error" in res:
                st.warning(res["Error"])
            else:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Stock Tested", res["Symbol"])
                m2.metric("Total Trades", res["Total Trades"])
                m3.metric("Win Rate", f"{res['Win Rate %']}%")
                m4.metric("Cumulative P&L Return", f"{res['Total Return %']}%")
                st.dataframe(res["Trades Ledger"], use_container_width=True)
