import importlib
import inspect
import streamlit as st
import pandas as pd
import engine as engine_module

# Streamlit keeps imported modules cached in the running server, so a redeploy
# or file change can leave an OLD engine module in memory. If the class is
# missing the index analyzer, force-reload the module from disk once.
if not hasattr(engine_module.PaperEngine, "analyze_index"):
    engine_module = importlib.reload(engine_module)
PaperEngine = engine_module.PaperEngine

st.set_page_config(page_title="NSE Stock & Index Options Scanner Engine", layout="wide")
st.title("⚡ Sammy - Multi-Asset Trading Engine")

# Rebuild the cached engine if this browser session is holding an object from an
# older deploy (session_state survives code updates until the server restarts).
if "engine" not in st.session_state or not hasattr(st.session_state.engine, "analyze_index"):
    st.session_state.engine = PaperEngine()

if not hasattr(st.session_state.engine, "analyze_index"):
    _path = inspect.getsourcefile(engine_module) or "unknown"
    _lines = []
    try:
        with open(_path, encoding="utf-8") as _f:
            _lines = _f.read().splitlines()
    except Exception:
        pass
    _hits = [(i + 1, l) for i, l in enumerate(_lines) if "def analyze_index" in l]
    if not _hits:
        _why = ("The engine.py this server is running does NOT contain `def analyze_index`. "
                "The server is running a different / older copy — check the file path below "
                "and that the app is deployed from the branch you pushed to, then reboot.")
    else:
        _indent = len(_hits[0][1]) - len(_hits[0][1].lstrip())
        if _indent == 0 or hasattr(engine_module, "analyze_index"):
            _why = ("`def analyze_index` is in engine.py but NOT indented inside `class PaperEngine` "
                    "(it must be indented 4 spaces, at the same level as `def analyze_stock`).")
        else:
            _why = ("`def analyze_index` is in the file but the class doesn't expose it — "
                    "reboot the app so the server reloads engine.py.")
    st.error("Engine mismatch: " + _why)
    st.code(f"engine file: {_path}\nanalyze_index found at line(s): {[n for n, _ in _hits]}\n"
            f"indent of first match: {(len(_hits[0][1]) - len(_hits[0][1].lstrip())) if _hits else 'n/a'} spaces\n"
            f"lines in file: {len(_lines)} (expected ~2051 for the new version)")
    st.stop()

class ScanDataError(Exception):
    pass

@st.cache_data(ttl=900, show_spinner=False)
def fetch_scan_results(index_name, top_n):
    """Cached for 15 minutes — but a scan where Yahoo returned little or no
    data raises instead, because Streamlit never caches exceptions. That stops
    an empty, failed scan from being served again on every click."""
    engine = st.session_state.engine
    universe = engine.fetch_nse_universe(index_name)
    results = engine.scan_all_strategies(universe, top_n=top_n)
    stats = engine.last_scan_stats
    if stats["total"] and stats["usable"] < 0.5 * stats["total"]:
        raise ScanDataError(
            f"Yahoo Finance returned usable data for only {stats['usable']} of {stats['total']} stocks "
            f"({stats['failed_batches']} of {stats['batches']} download batches failed). "
            "This is a data-source problem, not 'no setups'. Wait a minute and scan again."
        )
    return results, stats

def stock_scan_note(strategy_label):
    """One-line context shown under an empty stock tab."""
    stt = st.session_state.get("scan_stats")
    if not stt:
        return
    liquid = max(stt["liquid"], 1)
    st.caption(
        f"Scanned {stt['usable']} of {stt['total']} stocks at {stt['scanned_at']} "
        f"({stt['liquid']} liquid enough). {stt['above_200']} of them ({100 * stt['above_200'] / liquid:.0f}%) "
        f"are above their 200-day EMA and {stt['above_20']} ({100 * stt['above_20'] / liquid:.0f}%) above their 20-day EMA. "
        f"{strategy_label} setups are long-only, so a weak market gives few or none."
    )

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
force_fresh = st.sidebar.checkbox("Force fresh scan (skip 15-min cache)", value=False)
if st.sidebar.button("🚀 Run Equity Market Scan", disabled=scan_disabled):
    if force_fresh:
        fetch_scan_results.clear()
    with st.spinner(f"Scanning {selected_index} for top {max_results} setups..."):
        try:
            _res, _stats = fetch_scan_results(selected_index, max_results)
            st.session_state["scan_results"] = _res
            st.session_state["scan_stats"] = _stats
            st.sidebar.success(f"Scan complete — {_stats['usable']}/{_stats['total']} stocks analysed.")
        except ScanDataError as _e:
            st.sidebar.error(str(_e))
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
            stock_scan_note("Swing")
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
            stock_scan_note("Intraday")
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
            stock_scan_note("BTST")
    else:
        st.info("Click 'Run Equity Market Scan' to fetch setups.")

with tab4:
    st.subheader("Stock & Index Technical Diagnostic")
    st.caption("Enter an NSE stock symbol (e.g. RELIANCE), or NIFTY / SENSEX / BANKNIFTY for an index up/down read.")

    qcol1, qcol2, qcol3 = st.columns(3)
    quick = None
    if qcol1.button("📈 Analyze NIFTY"):
        quick = "NIFTY"
    if qcol2.button("📈 Analyze SENSEX"):
        quick = "SENSEX"
    if qcol3.button("📈 Analyze BANKNIFTY"):
        quick = "BANKNIFTY"

    symbol_input = st.text_input("Enter NSE Ticker Symbol or Index:", "RELIANCE")
    analyze_clicked = st.button("🔍 Analyze Stock / Index")  # always rendered, never short-circuited
    run_symbol = quick or (symbol_input if analyze_clicked else None)

    if run_symbol:
        with st.spinner(f"Analyzing {run_symbol}..."):
            res = st.session_state.engine.analyze_stock(run_symbol)
        if "Error" in res:
            st.error(res["Error"])
        elif res.get("Is_Index"):
            bias = res["Bias"]
            note = res.get("Bias_Note", bias)
            if bias == "UP":
                st.success(f"{res['Symbol']}: bias is {note} ({res['Bias_Score']}) — {res['Trend_Strength']}")
            elif bias == "DOWN":
                st.error(f"{res['Symbol']}: bias is {note} ({res['Bias_Score']}) — {res['Trend_Strength']}")
            else:
                st.warning(f"{res['Symbol']}: SIDEWAYS / MIXED ({res['Bias_Score']}) — {res['Trend_Strength']}")
            for w in res.get("Warnings", []):
                st.warning("⚠️ " + w)

            i1, i2, i3, i4, i5 = st.columns(5)
            i1.metric("Level", f"{res['Price']:,.2f}", f"{res['Day_Change_%']:+.2f}%")
            i2.metric("RSI (14, daily)", res["RSI"])
            i3.metric("ADX (14, daily)", res["ADX"])
            i4.metric("ATR (14, daily)", res["ATR"])
            i5.metric("200-day EMA", f"{res['EMA200']:,.2f}")
            _age = res.get("History_Cache_Age_Min")
            st.caption(
                f"Session analysed: {res['Session']} (latest available 5-min data; may lag the live tape). "
                f"15-year daily history cached {('%.0f' % _age) if _age is not None else '0'} min ago (refreshes hourly); "
                "today's bar and the 5-minute data are fetched live on every click."
            )

            st.markdown("### Trend Factors")
            for item in res["Checklist"]:
                if item.startswith("✓"):
                    st.success(item)
                elif item.startswith("✗"):
                    st.error(item)
                else:
                    st.info(item)

            st.markdown("### Key Levels")
            st.dataframe(
                pd.DataFrame(list(res["Levels"].items()), columns=["Level", "Value"]),
                use_container_width=True, hide_index=True,
            )
            hist = res.get("History")
            st.markdown("### 📊 What happened historically in similar conditions")
            if not hist:
                st.info("Not enough price history to compute base rates for this index.")
            else:
                stt = hist["state"]
                st.caption(
                    f"Today's daily setup: bias **{stt['bucket']}** (score {stt['daily_score']:+d}/5), "
                    f"RSI zone **{stt['rsi_zone']}**. Compared with {hist['years']} years of history "
                    f"(since {hist['since']}), using daily factors only."
                )
                if hist["strength"] == "lean":
                    st.success(hist["verdict"])
                elif hist["strength"] in ("none", "unstable"):
                    st.info(hist["verdict"])
                else:
                    st.warning(hist["verdict"])
                hdf = pd.DataFrame(hist["table"])
                st.dataframe(hdf, use_container_width=True, hide_index=True)
                st.caption(
                    "How to read this: '% Up' / '% Down' is how often the index closed higher / lower after that many trading days. "
                    "Compare each row with 'All days (baseline)' — an index rises on more than half of all days, so a "
                    "high '% Up' only matters if it beats the baseline. 'Episodes' counts separate occurrences "
                    "(neighbouring days are one episode), which is the real sample size. Percentages are hidden when "
                    "there are too few cases. Past base rates are not a promise of future results."
                )
            st.caption("A read of current conditions from price data — not a forecast. Yahoo provides no volume for indices, so volume is not used.")
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
    st.subheader("Index Options — CE / PE Calls")
    st.caption(
        "Scans NIFTY (Tuesday expiry) and SENSEX (Thursday expiry) on 5-minute charts. "
        "If a call exists you get the strike, stop loss and target; otherwise it says so."
    )

    if st.button("⚡ Scan CE/PE Calls"):
        calls = []
        with st.spinner("Scanning NIFTY and SENSEX..."):
            for _idx in ("NIFTY", "SENSEX"):
                try:
                    _sig = st.session_state.engine.evaluate_index_options(_idx)
                except Exception as _e:
                    _sig = None
                    st.error(f"Could not scan {_idx}: {_e}")
                if _sig:
                    calls.append(_sig)

        if not calls:
            st.info("No CE/PE calls as of now.")
            if not engine_module.is_market_open():
                st.caption("The NSE market is closed (Mon–Fri 09:15–15:30 IST), so there are no live calls.")
        else:
            for _sig in calls:
                _is_ce = _sig["Direction"] == "CE"
                _head = (f"{'🟢' if _is_ce else '🔴'} {_sig['Index']}: BUY {_sig['Direction']} "
                         f"({'bullish' if _is_ce else 'bearish'}) — {_sig['Contract Symbol']}")
                if _sig.get("Blocked Reason"):
                    st.warning(_head)
                    st.caption(f"Setup found, but not tradable right now: {_sig['Blocked Reason']}.")
                else:
                    st.success(_head)

                _risk = _sig["Premium (LTP)"] - _sig["Stop Loss"]
                _rr = (_sig["Target"] - _sig["Premium (LTP)"]) / _risk if _risk > 0 else 0
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Strike", f"{int(_sig['Strike'])} {_sig['Direction']}")
                m2.metric("Entry (premium)", f"₹{_sig['Premium (LTP)']}")
                m3.metric("Stop Loss", f"₹{_sig['Stop Loss']}")
                m4.metric("Target", f"₹{_sig['Target']}")

                _lots = _sig["Recommended Lots"]
                st.caption(
                    f"Expiry {_sig['Expiry']}{' (TODAY — expiry-day rules)' if _sig.get('Expiry Day') else ''} | "
                    f"Spot {_sig['Spot Price']:,.2f} | Lot size {_sig['Lot Size']} | Reward:Risk {_rr:.1f}:1"
                    + (f" | Suggested size {_lots} lot(s), risking ≈ ₹{_sig['Risk Amount']:,.0f}" if _lots else "")
                )
                _src = _sig.get("Premium Source", "")
                if _src.startswith("Model"):
                    st.caption("⚠️ Premium is a model estimate, not a live quote. Check the real option price before using these levels — scale stop loss and target from the actual premium (SL -25%, target +40%; expiry day -30% / +50%).")
                else:
                    st.caption(f"Premium source: {_src}.")
            st.caption("Educational paper-trading signals, not financial advice.")

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
        st.caption(
            "'entry' is the price/premium recorded when the trade was opened and never changes. "
            "'current' is a live mark (or, for options when NSE's live chain is unreachable — the "
            "common case on Render — a model-estimated premium) refreshed each time this tab loads."
        )
        if not open_df.empty:
            st.dataframe(open_df.drop(columns=["exit_time", "exit_price", "exit_reason"]), use_container_width=True)
        else:
            st.info("No open positions right now.")

        st.markdown("### ⚪ Closed Trades")
        if not closed_df.empty:
            st.dataframe(closed_df.drop(columns=["current", "unrealized_pnl"], errors="ignore"), use_container_width=True)
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
