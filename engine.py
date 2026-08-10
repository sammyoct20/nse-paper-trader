import pandas as pd
import yfinance as yf
import psycopg2
import os
import time
from datetime import datetime


class PaperEngine:

    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise Exception("DATABASE_URL not set")

        self.conn = psycopg2.connect(db_url)
        self.create_tables()

        self.capital = 100000
        self.risk_per_trade = 0.01

    # ---------------- DATABASE ---------------- #

    def create_tables(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY
        );
        """)

        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS symbol TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS target FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS qty INT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS status TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl FLOAT DEFAULT 0;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP;")

        self.conn.commit()
        cur.close()

    # ---------------- SCAN MARKET ---------------- #

    def scan_market(self):

        symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
            "SBIN.NS","ITC.NS","LT.NS","HCLTECH.NS","WIPRO.NS"
        ]

        signals = []

        for sym in symbols:
            try:
                time.sleep(1)  # prevent rate limit

                df = yf.download(sym, period="5d", interval="15m", progress=False)

                if df.empty or len(df) < 20:
                    continue

                close = float(df["Close"].iloc[-1])

                # 🔴 FORCE SIGNALS (for testing)
                signals.append({
                    "symbol": sym,
                    "price": close
                })

            except Exception as e:
                print(f"Error {sym}: {e}")

        return signals

    # ---------------- GENERATE TRADES ---------------- #

    def generate_trades(self, signals):

        trades = []

        for s in signals:
            entry = s["price"]

            sl = entry * 0.99
            target = entry * 1.02

            risk = entry - sl
            qty = int((self.capital * self.risk_per_trade) / risk) if risk > 0 else 0

            trades.append({
                "symbol": s["symbol"],
                "entry": round(entry, 2),
                "stop_loss": round(sl, 2),
                "target": round(target, 2),
                "qty": qty,
                "status": "OPEN"
            })

        return trades

    # ---------------- SAVE TRADES ---------------- #

    def save_trades(self, trades):

        cur = self.conn.cursor()

        for t in trades:

            cur.execute("""
            SELECT COUNT(*) FROM trades
            WHERE symbol=%s AND status='OPEN'
            """, (t["symbol"],))

            if cur.fetchone()[0] > 0:
                continue

            cur.execute("""
            INSERT INTO trades (symbol, entry, stop_loss, target, qty, status)
            VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                t["symbol"], t["entry"], t["stop_loss"],
                t["target"], t["qty"], t["status"]
            ))

        self.conn.commit()
        cur.close()

    # ---------------- UPDATE TRADES ---------------- #

    def update_trades(self):

        cur = self.conn.cursor()

        cur.execute("""
        SELECT id, symbol, entry, stop_loss, target, qty
        FROM trades WHERE status='OPEN'
        """)

        rows = cur.fetchall()

        for r in rows:
            trade_id, sym, entry, sl, target, qty = r

            try:
                time.sleep(1)

                df = yf.download(sym, period="1d", interval="5m", progress=False)
                if df.empty:
                    continue

                price = float(df["Close"].iloc[-1])

                if price <= sl or price >= target:

                    pnl = (price - entry) * qty

                    cur.execute("""
                    UPDATE trades
                    SET status='CLOSED', pnl=%s, exit_time=%s
                    WHERE id=%s
                    """, (pnl, datetime.now(), trade_id))

            except Exception as e:
                print("Update error:", e)

        self.conn.commit()
        cur.close()

    # ---------------- RUN ---------------- #

    def run_once(self):

        signals = self.scan_market()
        trades = self.generate_trades(signals)

        self.save_trades(trades)
        self.update_trades()
