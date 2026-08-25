import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Strategy Scanner Engine", layout="wide")
st.title("⚡Sammy - Multi-Strategy NSE Trading Engine")

if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

@st.cache_data(ttl=900)
def fetch_scan_results(index_name):
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    results = engine.scan_all_strategies(universe)
    return results

st.sidebar.header("Scan Controls")
selected_index = st.sidebar.selectbox("Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)

if st.sidebar.button("🚀 Run Market Scan"):
    with st.spinner(f"Scanning {selected_index}..."):
        scan_data = fetch_scan_results(selected_index)
        st.session_state["scan_results"] = scan_data
    st.sidebar.success("Scan Complete!")

tab1, tab2, tab3, tab4 = st.tabs(["📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer"])

with tab1:
    st.subheader("Swing Trading Candidates")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["SWING"], use_container_width=True)
    else:
        st.info("Click 'Run Market Scan' to fetch setups.")

with tab2:
    st.subheader("Intraday Momentum Setups")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["INTRADAY"], use_container_width=True)
    else:
        st.info("Click 'Run Market Scan' to fetch setups.")

with tab3:
    st.subheader("BTST Candidates")
    if "scan_results" in st.session_state:
        st.dataframe(st.session_state["scan_results"]["BTST"], use_container_width=True)
    else:
        st.info("Click 'Run Market Scan' to fetch setups.")

with tab4:
    st.subheader("Single Stock Technical Diagnostic")
    symbol_input = st.text_input("Enter NSE Ticker Symbol:", "RELIANCE")
    
    if st.button("Analyze Stock"):
        with st.spinner(f"Analyzing {symbol_input}..."):
            res = st.session_state.engine.analyze_stock(symbol_input)
            if "Error" in res:
                st.error(res["Error"])
            else:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Score", f"{res['Score']} / 100")
                col2.metric("Price", f"₹{res['Price']}")
                col3.metric("Stop Loss", f"₹{res['StopLoss']}")
                col4.metric("Target", f"₹{res['Target']}")
                st.markdown("### Criteria Breakdown")
                for r in res["Reasons"]:
                    st.write(r)
