import pandas as pd
import yfinance as yf
import psycopg2
import os
from datetime import datetime


class PaperEngine:

    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        self.conn = psycopg2.connect(db_url)
        self.create_tables()

        self.capital = 100000
        self.risk_per_trade = 0.01

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

        self.conn.commit()
        cur.close()

    # ---------------- SCAN (FAST) ---------------- #

    def scan_market(self):

    symbols = [
        "HDFCBANK.NS", "ICICIBANK.NS", "RELIANCE.NS", "BHARTIARTL.NS",
        "LT.NS", "SBIN.NS", "INFY.NS", "AXISBANK.NS",
        "BAJFINANCE.NS", "M&M.NS", "ADANIENT.NS", "ADANIPORTS.NS",
        "APOLLOHOSP.NS", "ASIANPAINT.NS", "BAJAJ-AUTO.NS", "BAJAJFINSV.NS",
        "BEL.NS", "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS",
        "EICHERMOT.NS", "ETERNAL.NS", "GRASIM.NS", "HCLTECH.NS",
        "HDFCLIFE.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ITC.NS",
        "INDIGO.NS", "JSWSTEEL.NS", "JIOFIN.NS", "KOTAKBANK.NS",
        "MARUTI.NS", "MAXHEALTH.NS", "NTPC.NS", "NESTLEIND.NS",
        "ONGC.NS", "POWERGRID.NS", "SBILIFE.NS", "SHRIRAMFIN.NS",
        "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TMPV.NS",
        "TATASTEEL.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS",
        "ULTRACEMCO.NS", "WIPRO.NS"
    ]

        print("Fetching data (batch)...")

        df = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="15m",
            group_by="ticker",
            threads=True
        )

        signals = []

        for sym in symbols:
            try:
                data = df[sym]

                if data.empty or len(data) < 30:
                    continue

                close = float(data["Close"].iloc[-1])
                ema20 = data["Close"].ewm(span=20).mean().iloc[-1]

                recent_high = data["High"].rolling(20).max().iloc[-2]

                # ✅ Better signal: trend + breakout
                if close > ema20 and close > recent_high:

                    signals.append({
                        "symbol": sym,
                        "price": close
                    })

            except Exception as e:
                print(f"{sym} error:", e)

        return signals

    # ---------------- TRADE ---------------- #

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

    # ---------------- SAVE ---------------- #

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

    # ---------------- UPDATE ---------------- #

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

        print("=== ENGINE START ===")

        signals = self.scan_market()
        print("Signals:", len(signals))

        trades = self.generate_trades(signals)
        print("Trades:", len(trades))

        self.save_trades(trades)
        self.update_trades()

        print("=== ENGINE END ===")
