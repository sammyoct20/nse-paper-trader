import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Strategy Scanner", layout="wide")

st.title("📈 Active Trading & Market Scanner Engine")

# Initialize engine instance in session state
if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

# Use Streamlit caching so user interactions do not trigger re-downloads
@st.cache_data(ttl=900)  # Cache data for 15 minutes
def run_cached_scan(index_name="NIFTY 50"):
    engine = st.session_state.engine
    tickers = engine.fetch_nse_universe(index_name)
    
    # Run scan on fetched tickers
    swing_df = engine.scan_markets(tickers=tickers, mode="SWING")
    intraday_df = engine.scan_markets(tickers=tickers, mode="INTRADAY")
    
    return swing_df, intraday_df

# Sidebar Controls
st.sidebar.header("Scan Settings")
selected_index = st.sidebar.selectbox("Select Index Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)

if st.sidebar.button("🚀 Run Market Scan"):
    with st.spinner(f"Scanning {selected_index} stocks... Please wait."):
        swing_df, intraday_df = run_cached_scan(selected_index)
        st.session_state["swing_results"] = swing_df
        st.session_state["intraday_results"] = intraday_df
    st.success("Scan Complete!")

# Display Results
tab1, tab2 = st.tabs(["Swing Setups", "Intraday Setups"])

with tab1:
    st.subheader("Swing Candidates")
    if "swing_results" in st.session_state:
        df = st.session_state["swing_results"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No swing candidates matched current technical criteria.")
    else:
        st.write("Click 'Run Market Scan' in the sidebar to fetch setups.")

with tab2:
    st.subheader("Intraday Candidates")
    if "intraday_results" in st.session_state:
        df = st.session_state["intraday_results"]
        if not df.empty:
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No intraday candidates matched current technical criteria.")
    else:
        st.write("Click 'Run Market Scan' in the sidebar to fetch setups.")
