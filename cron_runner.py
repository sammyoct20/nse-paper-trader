import os
import io
import warnings
import datetime
import requests
import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker

warnings.filterwarnings("ignore")

# 1. DATABASE CONFIGURATION
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///paper_trading.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

db_engine = create_engine(DATABASE_URL)
Base = declarative_base()

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20))
    strategy = Column(String(50))
    entry_price = Column(Float)
    current_price = Column(Float)
    stop_loss = Column(Float)
    target = Column(Float)
    quantity = Column(Integer)
    status = Column(String(20))  # OPEN, TARGET_HIT, SL_HIT
    entry_date = Column(DateTime, default=datetime.datetime.utcnow)
    exit_date = Column(DateTime, nullable=True)

# AUTO-MIGRATION: Drops legacy/incompatible table and recreates clean schema automatically
with db_engine.connect() as conn:
    try:
        # Check if the existing table has the correct entry_price column
        conn.execute(text("SELECT entry_price FROM trades LIMIT 1;"))
    except Exception:
        # Legacy schema detected - drop old table to rebuild with current columns
        print("Legacy schema detected. Dropping old table to rebuild clean structure...")
        conn.execute(text("DROP TABLE IF EXISTS trades CASCADE;"))
        conn.commit()

Base.metadata.create_all(db_engine)
Session = sessionmaker(bind=db_engine)

# 2. TELEGRAM NOTIFIER
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_alert(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")

# 3. MONITOR AND UPDATE ACTIVE POSITIONS
def update_open_positions():
    session = Session()
    open_trades = session.query(Trade).filter(Trade.status == "OPEN").all()
    
    if not open_trades:
        print("No open positions to monitor.")
        session.close()
        return

    print(f"Monitoring {len(open_trades)} active positions...")
    
    for trade in open_trades:
        ticker = f"{trade.symbol}.NS"
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        if df.empty:
            continue
            
        latest_close = float(df['Close'].iloc[-1])
        latest_high = float(df['High'].iloc[-1])
        latest_low = float(df['Low'].iloc[-1])
        
        # Check Stop Loss
        if latest_low <= trade.stop_loss:
            trade.status = "SL_HIT"
            trade.exit_date = datetime.datetime.utcnow()
            trade.current_price = trade.stop_loss
            pnl = round((trade.stop_loss - trade.entry_price) * trade.quantity, 2)
            send_telegram_alert(
                f"🔴 *STOP LOSS HIT*\n\n"
                f"Stock: `{trade.symbol}`\nStrategy: {trade.strategy}\n"
                f"Exit: ₹{trade.stop_loss}\nP&L: ₹{pnl}"
            )
            
        # Check Target
        elif latest_high >= trade.target:
            trade.status = "TARGET_HIT"
            trade.exit_date = datetime.datetime.utcnow()
            trade.current_price = trade.target
            pnl = round((trade.target - trade.entry_price) * trade.quantity, 2)
            send_telegram_alert(
                f"🟢 *TARGET HIT*\n\n"
                f"Stock: `{trade.symbol}`\nStrategy: {trade.strategy}\n"
                f"Exit: ₹{trade.target}\nP&L: ₹{pnl}"
            )
        else:
            trade.current_price = latest_close

    session.commit()
    session.close()

# 4. RUN SCANNER & EXECUTE NEW TRADES
def run_automated_scan():
    from engine import PaperEngine
    
    print("Running market scan across NIFTY 50...")
    engine = PaperEngine()
    universe = engine.fetch_nse_universe("NIFTY 50")
    results = engine.scan_all_strategies(universe)
    
    session = Session()
    
    # Process BTST Setups
    btst_df = results.get("BTST", pd.DataFrame())
    if not btst_df.empty:
        for _, row in btst_df.head(2).iterrows():  # Max 2 top setups
            symbol = row["Ticker"]
            existing = session.query(Trade).filter(Trade.symbol == symbol, Trade.status == "OPEN").first()
            if not existing and row["Qty"] > 0:
                new_trade = Trade(
                    symbol=symbol,
                    strategy="BTST",
                    entry_price=row["Price"],
                    current_price=row["Price"],
                    stop_loss=row["StopLoss"],
                    target=row["Target"],
                    quantity=row["Qty"],
                    status="OPEN"
                )
                session.add(new_trade)
                send_telegram_alert(
                    f"🚀 *AUTOMATED PAPER TRADE ENTERED*\n\n"
                    f"Stock: `{symbol}`\nStrategy: BTST\n"
                    f"Entry: ₹{row['Price']}\nQty: {row['Qty']}\n"
                    f"Stop Loss: ₹{row['StopLoss']}\nTarget: ₹{row['Target']}"
                )

    session.commit()
    session.close()

if __name__ == "__main__":
    print("Starting Automated Paper Trading Execution...")
    update_open_positions()
    run_automated_scan()
    print("Execution Finished Successfully.")
