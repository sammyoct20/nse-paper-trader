import streamlit as st
from engine import PaperEngine
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(page_title="NSE Scanner", layout="wide")

st.title("📈 NSE Swing Scanner")

# ---- Time ----
now = datetime.now(ZoneInfo("Asia/Kolkata"))
st.write(f"🕒 Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")

# ---- Button ----
if st.button("Run Scanner"):

    with st.spinner("Scanning market..."):
        engine = PaperEngine()
        signals = engine.scan_market()

    if signals:
        st.success(f"Found {len(signals)} signals")

        for s in signals:
            st.markdown("---")
            st.subheader(s["symbol"])

            col1, col2, col3, col4 = st.columns(4)

            col1.metric("Entry", s["entry"])
            col2.metric("Stop Loss", s["stop_loss"])
            col3.metric("Target", s["target"])
            col4.metric("Qty", s["qty"])

            st.write(f"Score: {s['score']} | Risk %: {s['risk_pct']}")

    else:
        st.warning("No signals found")

else:
    st.info("Click 'Run Scanner' to scan market")
