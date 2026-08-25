import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Strategy Scanner Engine", layout="wide")

st.title("⚡ Multi-Strategy NSE Trading Engine")

# Initialize persistent session engine
if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

# Cache market scans for 15 minutes to preserve performance
@st.cache_data(ttl=900)
def fetch_scan_results(index_name):
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    results = engine.scan_all_strategies(universe)
    return results

# Sidebar Controls
st.sidebar.header("Scan Parameters")
selected_index = st.sidebar.selectbox("Universe Selection", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)

if st.sidebar.button("🚀 Run Full Market Scan"):
    with st.spinner(f"Running multi-strategy scan on {selected_index}..."):
        scan_data = fetch_scan_results(selected_index)
        st.session_state["scan_results"] = scan_data
    st.sidebar.success("Scan Completed!")

# Main Tabs Setup
tab1, tab2, tab3, tab4 = st.tabs(["📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer"])

# TAB 1: SWING
with tab1:
    st.subheader("Swing Trading Opportunities")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["SWING"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched Swing strategy criteria.")
    else:
        st.info("Click 'Run Full Market Scan' in the sidebar to populate setups.")

# TAB 2: INTRADAY
with tab2:
    st.subheader("Intraday Momentum Setups")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["INTRADAY"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched Intraday strategy criteria.")
    else:
        st.info("Click 'Run Full Market Scan' in the sidebar to populate setups.")

# TAB 3: BTST
with tab3:
    st.subheader("Buy Today Sell Tomorrow (BTST) Candidates")
    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]["BTST"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No stocks matched BTST strategy criteria.")
    else:
        st.info("Click 'Run Full Market Scan' in the sidebar to populate setups.")

# TAB 4: STOCK ANALYZER
with tab4:
    st.subheader("Single Stock Technical Diagnostic")
    symbol_input = st.text_input("Enter NSE Ticker Symbol (e.g., RELIANCE, TATAMOTORS, INFY):", "RELIANCE")
    
    if st.button("Analyze Stock"):
        with st.spinner(f"Analyzing {symbol_input}..."):
            res = st.session_state.engine.analyze_stock(symbol_input)
            
            if "Error" in res:
                st.error(res["Error"])
            else:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Technical Score", f"{res['Score']} / 100")
                col2.metric("Last Price", f"₹{res['Price']}")
                col3.metric("Stop Loss", f"₹{res['StopLoss']}")
                col4.metric("Target", f"₹{res['Target']}")
                
                st.markdown("### Strategy Alignment Breakdown")
                for r in res["Reasons"]:
                    st.write(r)
