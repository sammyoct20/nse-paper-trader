import streamlit as st
import pandas as pd
import psycopg2
import os
from engine import PaperEngine

st.set_page_config(layout="wide")
st.title("📊 Trading Dashboard")

# ---------------- DB ---------------- #
@st.cache_resource
def get_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

conn = get_connection()

# ---------------- UI ---------------- #
st.write("UI loaded")

if st.button("Run Scanner Now"):
    try:
        st.write("Running scanner...")

        engine = PaperEngine()

        signals = engine.scan_market()
        st.write(f"Signals: {len(signals)}")

        trades = engine.generate_trades(signals)
        st.write(f"Trades generated: {len(trades)}")

        engine.save_trades(trades)
        st.write("Trades saved")

        engine.update_trades()
        st.write("Trades updated")

        st.success("Done")

    except Exception as e:
        st.error(f"Error: {e}")

# ---------------- DATA ---------------- #
try:
    df = pd.read_sql("SELECT * FROM trades ORDER BY created_at DESC", conn)
except Exception as e:
    st.error(f"DB Error: {e}")
    st.stop()

if df.empty:
    st.warning("No trades yet")
    st.stop()

st.dataframe(df, use_container_width=True)
