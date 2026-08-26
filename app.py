import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Filtered Scanner Engine", layout="wide")
st.title("⚡ Multi-Strategy NSE Trading Engine")

if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

@st.cache_data(ttl=900)
def fetch_scan_results(index_name, top_n):
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    results = engine.scan_all_strategies(universe, top_n=top_n)
    return results

st.sidebar.header("Scan Parameters")
selected_index = st.sidebar.selectbox("Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)
max_results = st.sidebar.slider("Max Stock Output Per Strategy", min_value=3, max_value=15, value=5)

if st.sidebar.button("🚀 Run Filtered Market Scan"):
    with st.spinner(f"Scanning {selected_index} for top {max_results} setups..."):
        scan_data = fetch_scan_results(selected_index, max_results)
        st.session_state["scan_results"] = scan_data
    st.sidebar.success("Scan Complete!")

tab1, tab2, tab3, tab4 = st.tabs(["📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer"])

with tab1:
    st.subheader(f"Top {max_results} Swing Trading Candidates")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["SWING"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict Swing criteria.")
    else:
        st.info("Click 'Run Filtered Market Scan' to fetch setups.")

with tab2:
    st.subheader(f"Top {max_results} Intraday Momentum Setups")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["INTRADAY"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict Intraday criteria.")
    else:
        st.info("Click 'Run Filtered Market Scan' to fetch setups.")

with tab3:
    st.subheader(f"Top {max_results} BTST Candidates")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["BTST"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched strict BTST criteria.")
    else:
        st.info("Click 'Run Filtered Market Scan' to fetch setups.")

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
