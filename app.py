import streamlit as st
import pandas as pd
from db import get_conn, create_tables
from engine import PaperEngine

st.set_page_config(layout="wide")
create_tables()

st.title("📊 Trading Dashboard - Sammy")

engine = PaperEngine()

if st.button("🚀 Run Scanner"):
    engine.run()
    st.success("Scanner executed")

conn = get_conn()
df = pd.read_sql("SELECT * FROM trades", conn)
conn.close()

def clean(df):
    if df.empty:
        return df

    df["symbol"] = df["symbol"].astype(str).str.replace(".NS","",regex=False)

    for col in ["entry","sl","target","entry_price","exit_price","pnl"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df

df = clean(df)

open_df = df[df["status"]=="OPEN"]
closed_df = df[df["status"]=="CLOSED"]

intraday_df = open_df[open_df["type"]=="INTRADAY"]
swing_df = open_df[open_df["type"]=="SWING"]

tab1, tab2, tab3, tab4 = st.tabs(["⚡ Intraday","📈 Swing","📁 Closed","📊 Performance"])

with tab1:
    st.dataframe(intraday_df, use_container_width=True)

with tab2:
    st.dataframe(swing_df, use_container_width=True)

with tab3:
    st.dataframe(closed_df, use_container_width=True)

with tab4:
    if not closed_df.empty:
        st.metric("Total PnL", round(closed_df["pnl"].sum(),2))
        st.metric("Win Rate", round((closed_df["pnl"]>0).mean()*100,2))
