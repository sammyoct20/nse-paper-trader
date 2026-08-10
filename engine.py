import pandas as pd
import numpy as np
import yfinance as yf
import psycopg2
import os
from datetime import datetime

class PaperEngine:

    def __init__(self):
        print("=== ENGINE INIT ===")

        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            raise Exception("DATABASE_URL not set")

        self.conn = psycopg2.connect(self.db_url)
        self.create_tables()

        self.capital = 100000
        self.risk_per_trade = 0.01  # 1%

    # ---------------- DB ---------------- #

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
            pnl FLOAT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exit_time TIMESTAMP
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            price FLOAT,
            score INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        self.conn.commit()
        cur.close()

    # ---------------- STRATEGY ---------------- #

    def scan_market(self):
        print("Scanning NIFTY 50...")

        symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
            "SBIN.NS","ITC.NS","LT.NS","HCLTECH.NS","WIPRO.NS",
            "TATASTEEL.NS","JSWSTEEL.NS","AXISBANK.NS","KOTAKBANK.NS",
            "MARUTI.NS","BAJAJ-AUTO.NS","TITAN.NS","ULTRACEMCO.NS",
            "ASIANPAINT.NS","SUNPHARMA.NS"
        ]

        signals = []

        for sym in symbols:
            try:
                df = yf.download(sym, period="10d", interval="15m", progress=False)

                if df.empty or len(df) < 20:
                    continue

                df["EMA20"] = df["Close"].ewm(span=20).mean()
                df["EMA50"] = df["Close"].ewm(span=50).mean()
                df["RSI"] = self.rsi(df["Close"])

                latest = df.iloc[-1]

                score = 0

                if latest["Close"] > latest["EMA20"]:
                    score += 1
                if latest["EMA20"] > latest["EMA50"]:
                    score += 1
                if 50 < latest["RSI"] < 70:
                    score += 1

                if score >= 2:
                    signals.append({
                        "symbol": sym,
                        "price": float(latest["Close"]),
                        "score": score
                    })

            except Exception as e:
                print(f"Error {sym}: {e}")

        return signals

    def rsi(self, series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    # ---------------- TRADING ---------------- #

    def generate_trades(self, signals):
        trades = []

        for s in signals:
            entry = s["price"]

            sl = entry * 0.99
            target = entry * 1.02

            risk_per_share = entry - sl
            capital_risk = self.capital * self.risk_per_trade

            qty = int(capital_risk / risk_per_share) if risk_per_share > 0 else 0

            trades.append({
                "symbol": s["symbol"],
                "entry": entry,
                "stop_loss": sl,
                "target": target,
                "qty": qty,
                "status": "OPEN"
            })

        return trades

    def save_trades(self, trades):
        cur = self.conn.cursor()

        for t in trades:
            # prevent duplicates
            cur.execute("""
                SELECT COUNT(*) FROM trades
                WHERE symbol=%s AND status='OPEN'
            """, (t["symbol"],))
            exists = cur.fetchone()[0]

            if exists > 0:
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

    def update_trades(self):
        cur = self.conn.cursor()

        cur.execute("""
            SELECT id, symbol, entry, stop_loss, target, qty
            FROM trades WHERE status='OPEN'
        """)

        rows = cur.fetchall()

        for r in rows:
            id, sym, entry, sl, target, qty = r

            try:
                df = yf.download(sym, period="1d", interval="5m", progress=False)
                if df.empty:
                    continue

                price = df["Close"].iloc[-1]

                if price <= sl:
                    pnl = (price - entry) * qty
                    cur.execute("""
                        UPDATE trades
                        SET status='CLOSED', pnl=%s, exit_time=NOW()
                        WHERE id=%s
                    """, (pnl, id))

                elif price >= target:
                    pnl = (price - entry) * qty
                    cur.execute("""
                        UPDATE trades
                        SET status='CLOSED', pnl=%s, exit_time=NOW()
                        WHERE id=%s
                    """, (pnl, id))

            except Exception as e:
                print("Update error:", e)

        self.conn.commit()
        cur.close()

    # ---------------- RUN ---------------- #

    def run_once(self):
        print("=== ENGINE START ===")

        signals = self.scan_market()
        print("Signals:", len(signals))

        trades = self.generate_trades(signals)

        self.save_trades(trades)
        self.update_trades()

        print("=== ENGINE END ===")
