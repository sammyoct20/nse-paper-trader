import streamlit as st
import pandas as pd
from db import get_conn, create_tables
from engine import PaperEngine

st.set_page_config(layout="wide")
create_tables()

st.title("📊 Enhanced Trading Dashboard")

engine = PaperEngine()

if st.button("🚀 Run Scanner"):
    engine.run()
    st.success("Scanner executed with updated multi-parameter model.")

conn = get_conn()
df = pd.read_sql("SELECT * FROM trades", conn)
conn.close()

def clean(df_in):
    if df_in.empty:
        return df_in

    df_in["symbol"] = df_in["symbol"].astype(str).str.replace(".NS", "", regex=False)
    num_cols = ["entry", "sl", "target", "entry_price", "exit_price", "pnl", "atr", "adx", "volume_ratio"]
    for col in num_cols:
        if col in df_in.columns:
            df_in[col] = pd.to_numeric(df_in[col], errors="coerce").round(2)

    return df_in

df = clean(df)

open_df = df[df["status"] == "OPEN"] if not df.empty else pd.DataFrame()
closed_df = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()

intraday_df = open_df[open_df["type"] == "INTRADAY"] if not open_df.empty else pd.DataFrame()
swing_df = open_df[open_df["type"] == "SWING"] if not open_df.empty else pd.DataFrame()

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["⚡ Intraday", "📈 Swing", "📁 Closed", "📊 Performance", "🔍 Stock Analyzer"]
)

with tab1:
    st.dataframe(intraday_df, use_container_width=True)

with tab2:
    st.dataframe(swing_df, use_container_width=True)

with tab3:
    st.dataframe(closed_df, use_container_width=True)

with tab4:
    if not closed_df.empty:
        col1, col2 = st.columns(2)
        col1.metric("Total PnL", f"₹{round(closed_df['pnl'].sum(), 2)}")
        col2.metric("Win Rate", f"{round((closed_df['pnl'] > 0).mean() * 100, 2)}%")
    else:
        st.info("No closed trades recorded yet.")

with tab5:
    symbol = st.text_input("Enter NSE/BSE Symbol (e.g. ICICIBANK, TATAMOTORS)").strip()

    if st.button("Analyze Stock"):
        if symbol:
            result = engine.analyze_stock(symbol)
            if "error" in result:
                st.error(result["error"])
            else:
                st.subheader(f"Analysis Results for {result['symbol']}")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Price", f"₹{result['price']}")
                col2.metric("Action", result["action"])
                col3.metric("ADX (Trend Power)", result["ADX"])
                col4.metric("Vol Multiplier", result["volume_multiplier"])

                st.json(result)
        else:
            st.warning("Please specify a stock ticker symbol.")
