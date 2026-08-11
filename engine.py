import yfinance as yf
import pandas as pd
import psycopg2
import os
from datetime import datetime

class PaperEngine:

    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.conn = psycopg2.connect(self.db_url)
        self.create_tables()

        # config
        self.capital = 100000
        self.max_trades = 3
        self.risk_per_trade = 0.01  # 1%

        # Nifty 50 sample (you can expand)
        self.symbols = [
            "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS",
            "INFY.NS", "TCS.NS", "LT.NS"
        ]

    # -----------------------------
    # DB TABLES
    # -----------------------------
    def create_tables(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            entry FLOAT,
            stop_loss FLOAT,
            target FLOAT,
            qty INT,
            status TEXT,
            pnl FLOAT,
            exit_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exit_time TIMESTAMP
        );
        """)

        self.conn.commit()
        cur.close()

    # -----------------------------
    # FETCH PRICE
    # -----------------------------
    def get_price(self, sym):
        try:
            df = yf.download(sym, period="1d", interval="5m", progress=False)
            if df.empty:
                return None
            return float(df["Close"].iloc[-1])
        except:
            return None

    # -----------------------------
    # STRATEGY
    # -----------------------------
    def generate_signal(self, sym):

        df = yf.download(sym, period="5d", interval="15m", progress=False)
        if df.empty or len(df) < 20:
            return None

        df["ema20"] = df["Close"].ewm(span=20).mean()
        df["ema50"] = df["Close"].ewm(span=50).mean()

        latest = df.iloc[-1]

        if latest["Close"] > latest["ema20"] > latest["ema50"]:
            return "BUY"

        return None

    # -----------------------------
    # OPEN TRADES COUNT
    # -----------------------------
    def open_trades_count(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        count = cur.fetchone()[0]
        cur.close()
        return count

    # -----------------------------
    # CREATE TRADE
    # -----------------------------
    def create_trade(self, sym, price):

        stop_loss = price * 0.99
        target = price * 1.02

        risk_amt = self.capital * self.risk_per_trade
        qty = int(risk_amt / (price - stop_loss))

        cur = self.conn.cursor()

        cur.execute("""
        INSERT INTO trades (symbol, entry, stop_loss, target, qty, status, pnl)
        VALUES (%s,%s,%s,%s,%s,'OPEN',0)
        """, (sym, price, stop_loss, target, qty))

        self.conn.commit()
        cur.close()

    # -----------------------------
    # UPDATE TRADES (AUTO CLOSE)
    # -----------------------------
    def update_trades(self):

        cur = self.conn.cursor()

        cur.execute("SELECT id, symbol, entry, stop_loss, target, qty FROM trades WHERE status='OPEN'")
        trades = cur.fetchall()

        for t in trades:
            trade_id, sym, entry, sl, tgt, qty = t

            price = self.get_price(sym)
            if price is None:
                continue

            exit_reason = None

            if price <= sl:
                exit_reason = "STOP LOSS HIT"

            elif price >= tgt:
                exit_reason = "TARGET HIT"

            if exit_reason:
                pnl = (price - entry) * qty

                print(f"Closing {sym} | {exit_reason}")

                cur.execute("""
                UPDATE trades
                SET status='CLOSED',
                    pnl=%s,
                    exit_reason=%s,
                    exit_time=%s
                WHERE id=%s
                """, (pnl, exit_reason, datetime.now(), trade_id))

        self.conn.commit()
        cur.close()

    # -----------------------------
    # MAIN RUN
    # -----------------------------
    def run_once(self):

        self.update_trades()

        trades_open = self.open_trades_count()

        signals = 0
        trades_created = 0

        for sym in self.symbols:

            if trades_open >= self.max_trades:
                break

            signal = self.generate_signal(sym)

            if signal == "BUY":
                price = self.get_price(sym)
                if price is None:
                    continue

                self.create_trade(sym, price)

                signals += 1
                trades_created += 1
                trades_open += 1

        # DEBUG
        cur = self.conn.cursor()
        cur.execute("SELECT symbol, status, pnl, exit_reason FROM trades")
        rows = cur.fetchall()
        print("==== DB DATA ====")
        for r in rows:
            print(r)
        cur.close()

        return signals, trades_created
