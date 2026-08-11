import streamlit as st
import pandas as pd
from engine import PaperEngine

st.set_page_config(page_title="Swing Scanner", layout="wide")

st.title("📊 Nifty 50 Swing Trading Scanner")

engine = PaperEngine()

# Run button
if st.button("Run Scanner"):

    with st.spinner("Scanning market..."):

        trades = engine.run()

    # No trades case
    if not trades:
        st.warning("❌ No trades found (Market weak or no setups)")
    else:
        df = pd.DataFrame(trades)

        st.success(f"✅ {len(df)} trades found")

        # Show top trades
        st.dataframe(df, use_container_width=True)

        # Download CSV
        csv = df.to_csv(index=False).encode("utf-8")

        st.download_button(
            label="Download Trades CSV",
            data=csv,
            file_name="swing_trades.csv",
            mime="text/csv"
        )
