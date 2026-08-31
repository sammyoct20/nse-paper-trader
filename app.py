import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="NSE Stock & Index Options Scanner Engine", layout="wide")
st.title("⚡ Sammy - Multi-Asset Trading Engine")

if "engine" not in st.session_state:
    st.session_state.engine = PaperEngine()

@st.cache_data(ttl=900)
def fetch_scan_results(index_name, top_n):
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    return engine.scan_all_strategies(universe, top_n=top_n)

st.sidebar.header("🛡️ Risk Management (Kotegawa Rules)")
risk_status = st.session_state.engine.risk_status()
rc1, rc2 = st.sidebar.columns(2)
rc1.metric("Risk / Trade", f"{risk_status['risk_per_trade_pct']}%", f"₹{risk_status['risk_per_trade_amount']:,.0f}")
rc2.metric("Max Position", f"{risk_status['max_position_pct']}%", f"₹{risk_status['max_position_value']:,.0f}")
rc3, rc4 = st.sidebar.columns(2)
rc3.metric("Open Positions", f"{risk_status['open_positions']}/{risk_status['max_open_positions']}")
rc4.metric("Today's PnL", f"₹{risk_status['daily_realized_pnl']:,.0f}", f"limit ₹{-risk_status['daily_loss_limit']:,.0f}")

if risk_status["circuit_breaker_tripped"]:
    st.sidebar.error("🚫 Daily loss circuit breaker TRIPPED — new entries blocked until tomorrow.")
elif risk_status["open_positions"] >= risk_status["max_open_positions"]:
    st.sidebar.warning("⚠️ Max open positions reached — new entries blocked.")
else:
    st.sidebar.success("✅ Risk gate open — new entries allowed.")

st.sidebar.divider()
st.sidebar.header("Scan Parameters")
selected_index = st.sidebar.selectbox("Universe", ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 500"], index=0)
max_results = st.sidebar.slider("Max Output Per Strategy", min_value=3, max_value=15, value=5)

scan_disabled = risk_status["circuit_breaker_tripped"]
if st.sidebar.button("🚀 Run Equity Market Scan", disabled=scan_disabled):
    with st.spinner(f"Scanning {selected_index} for top {max_results} setups..."):
        st.session_state["scan_results"] = fetch_scan_results(selected_index, max_results)
    st.sidebar.success("Scan Complete!")
if scan_disabled:
    st.sidebar.caption("Scanning is disabled while the circuit breaker is tripped. Setups can still exceed capital risk limits; wait for tomorrow's reset.")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Swing Trade", "⚡ Intraday", "🌙 BTST Setups", "🔍 Stock Analyzer", "📈 Index Options (CE/PE)", "📒 Paper Trading Account"
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
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Price", f"₹{res['Price']}")
                c2.metric("Technical Score", res['Score'])
                c3.metric("RSI (14)", res['RSI'])
                c4.metric("Stop Loss", f"₹{res['StopLoss']}")
                c5.metric("Target", f"₹{res['Target']}")

                c6, c7, c8 = st.columns(3)
                c6.metric("Kotegawa-Sized Qty", res['Qty'])
                c7.metric("Risk Amount", f"₹{res['RiskAmount']}")
                c8.metric("Position Value", f"₹{res['PositionValue']}")
                if res['Qty'] == 0:
                    st.caption("Qty is 0 because the risk gate is currently closed (circuit breaker tripped or max open positions reached) — see the sidebar.")

                st.markdown("### Technical Setup Checklist")
                for item in res["Checklist"]:
                    if item.startswith("✓"):
                        st.success(item)
                    else:
                        st.error(item)

with tab5:
    st.subheader("NIFTY (Tuesday Expiry) & SENSEX (Thursday Expiry) Signals")
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Strategy: 5-Min Trend + RSI Momentum Breakout (CE/PE)")
    with col2:
        st.caption("Lot Sizes: NIFTY = 65 | SENSEX = 20")

    idx_select = st.selectbox("Select Index:", ["NIFTY", "SENSEX"])
    if st.button(f"⚡ Scan Live {idx_select} Options Signal"):
        with st.spinner(f"Evaluating 5m charts for {idx_select}..."):
            signal = st.session_state.engine.evaluate_index_options(idx_select)
            if signal:
                if signal.get("Blocked Reason"):
                    st.warning(f"Setup found but blocked: {signal['Blocked Reason']}")
                else:
                    st.success(f"Trade Execution Contract: {signal['Contract Symbol']}")
                st.json(signal)
            else:
                st.warning(f"No clear CE/PE directional breakout detected for {idx_select} right now.")

with tab6:
    st.subheader("Paper Trading Account")
    st.caption(
        "Positions are opened and auto squared-off by the scheduled GitHub Actions worker "
        "(`worker.py`, every 5 min during market hours) — not by this dashboard. This tab is read-only."
    )

    if st.button("🔄 Refresh Account"):
        st.rerun()

    rs = st.session_state.engine.risk_status()
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Current Capital", f"₹{rs['capital']:,.0f}")
    a2.metric("Today's Realized PnL", f"₹{rs['daily_realized_pnl']:,.0f}")
    a3.metric("Open Positions", f"{rs['open_positions']}/{rs['max_open_positions']}")
    a4.metric("Circuit Breaker", "🚫 TRIPPED" if rs["circuit_breaker_tripped"] else "✅ OK")

    log_df = st.session_state.engine.get_trade_log(limit=200)

    if log_df.empty:
        st.info("No paper trades yet. They'll appear here once the scheduled worker opens and closes positions.")
    else:
        open_df = log_df[log_df["status"] == "OPEN"]
        closed_df = log_df[log_df["status"] == "CLOSED"]

        st.markdown("### 🟢 Open Positions")
        if not open_df.empty:
            st.dataframe(open_df.drop(columns=["exit_time", "exit_price", "exit_reason"]), use_container_width=True)
        else:
            st.info("No open positions right now.")

        st.markdown("### ⚪ Closed Trades")
        if not closed_df.empty:
            st.dataframe(closed_df, use_container_width=True)
            wins = (closed_df["pnl"] > 0).sum()
            total_closed = len(closed_df)
            win_rate = round(100 * wins / total_closed, 1) if total_closed else 0.0
            total_pnl = round(closed_df["pnl"].sum(), 2)
            b1, b2, b3 = st.columns(3)
            b1.metric("Closed Trades", total_closed)
            b2.metric("Win Rate", f"{win_rate}%")
            b3.metric("Total Realized PnL", f"₹{total_pnl:,.0f}")
        else:
            st.info("No closed trades yet.")
