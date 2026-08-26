import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Stock & Index Options Scanner Engine", layout="wide")
st.title("⚡ Sammy - Multi-Asset NSE Trading Engine")

if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

@st.cache_data(ttl=900)
def fetch_scan_results(index_name, top_n):
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    return engine.scan_all_strategies(universe, top_n=top_n)

st.sidebar.header("Scan Parameters")
selected_index = st.sidebar.selectbox("Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)
max_results = st.sidebar.slider("Max Output Per Strategy", min_value=3, max_value=15, value=5)

if st.sidebar.button("🚀 Run Equity Market Scan"):
    with st.spinner(f"Scanning {selected_index} for top {max_results} setups..."):
        st.session_state["scan_results"] = fetch_scan_results(selected_index, max_results)
    st.sidebar.success("Scan Complete!")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer", "📈 Index Options (CE/PE)"
])

with tab1:
    st.subheader(f"Top {max_results} Swing Trading Candidates")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["SWING"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict Swing criteria.")
    else:
        st.info("Click 'Run Equity Market Scan' to fetch setups.")

with tab2:
    st.subheader(f"Top {max_results} Intraday Momentum Setups")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["INTRADAY"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict Intraday criteria.")
    else:
        st.info("Click 'Run Equity Market Scan' to fetch setups.")

with tab3:
    st.subheader(f"Top {max_results} BTST Candidates")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["BTST"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict BTST criteria.")
    else:
        st.info("Click 'Run Equity Market Scan' to fetch setups.")

with tab4:
    st.subheader("Single Stock Technical Diagnostic")
    symbol_input = st.text_input("Enter NSE Ticker Symbol:", "RELIANCE")
    if st.button("Analyze Stock"):
        with st.spinner(f"Analyzing {symbol_input}..."):
            res = st.session_state.engine.analyze_stock(symbol_input)
            if "Error" in res:
                st.error(res["Error"])
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Price", f"₹{res['Price']}")
                c2.metric("RSI (14)", res['RSI'])
                c3.metric("Stop Loss", f"₹{res['StopLoss']}")
                c4.metric("Target", f"₹{res['Target']}")

with tab5:
    st.subheader("NIFTY & BANKNIFTY Real-Time Index Options Signals")
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Strategy: 5-Min Trend + RSI Momentum Breakout (CE/PE)")
    with col2:
        st.caption("Data Source: Direct Live NSE Option Chain")

    idx_select = st.selectbox("Select Index:", ["NIFTY", "BANKNIFTY"])
    if st.button(f"⚡ Scan Live {idx_select} Options Signal"):
        with st.spinner(f"Evaluating 5m charts and NSE Live Option Chain for {idx_select}..."):
            signal = st.session_state.engine.evaluate_index_options(idx_select)
            if signal:
                st.success(f"Directional Signal Triggered: {signal['Direction']}!")
                st.json(signal)
            else:
                st.warning(f"No clear CE/PE directional breakout detected for {idx_select} right now.")
