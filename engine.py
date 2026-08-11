import pandas as pd
import yfinance as yf
import psycopg2
import os
from datetime import datetime

class PaperEngine:

    def __init__(self):
        self.conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        self.create_tables()

        self.total_capital = 100000
        self.max_trades = 3

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

    # ---------------- STOCK LIST ---------------- #
    def get_nifty50(self):
        return [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
            "SBIN.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS","LT.NS",
            "HCLTECH.NS","AXISBANK.NS","ASIANPAINT.NS","MARUTI.NS","SUNPHARMA.NS"
        ]

    # ---------------- INDICATORS ---------------- #
    def add_indicators(self, df):
        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        df["EMA200"] = df["Close"].ewm(span=200).mean()

        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df["VOL_AVG"] = df["Volume"].rolling(20).mean()
        df["HH"] = df["High"].rolling(20).max()

        return df

    # ---------------- SCORING ---------------- #
    def score_stock(self, df):
        last = df.iloc[-1]
        score = 0

        if last["Close"] > last["EMA50"]:
            score += 2
        if last["EMA50"] > last["EMA200"]:
            score += 2
        if 55 < last["RSI"] < 70:
            score += 1
        if last["Volume"] > 1.2 * last["VOL_AVG"]:
            score += 2
        if last["Close"] > df["HH"].iloc[-2]:
            score += 3

        return score

    # ---------------- SCAN ---------------- #
    def scan_market(self):
        symbols = self.get_nifty50()

        df_all = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="15m",
            group_by="ticker",
            auto_adjust=False,
            threads=True
        )

        scored = []

        for sym in symbols:
            try:
                df = df_all[sym]

                if df.empty or len(df) < 50:
                    continue

                df = self.add_indicators(df)

                score = self.score_stock(df)

                if score >= 6:
                    price = float(df["Close"].iloc[-1])
                    scored.append((sym, price, score))

            except:
                continue

        scored.sort(key=lambda x: x[2], reverse=True)

        return scored

    # ---------------- GENERATE TRADES ---------------- #
    def generate_trades(self, signals):

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        open_count = cur.fetchone()[0]
        cur.close()

        slots = self.max_trades - open_count

        if slots <= 0:
            return []

        signals = signals[:slots]

        capital_per_trade = self.total_capital / self.max_trades

        trades = []

        for sym, price, score in signals:

            stop_loss = price * 0.98
            target = price * 1.03
            qty = int(capital_per_trade / price)

            trades.append({
                "symbol": sym,
                "entry": round(price, 2),
                "stop_loss": round(stop_loss, 2),
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

    # ---------------- UPDATE (FIXED SL LOGIC) ---------------- #
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
                df = yf.download(
                    sym,
                    period="1d",
                    interval="5m",
                    auto_adjust=False,
                    progress=False
                )

                if df.empty:
                    continue

                low = float(df["Low"].iloc[-1])
                high = float(df["High"].iloc[-1])

                exit_price = None

                # SL HIT (intra candle)
                if low <= sl:
                    exit_price = sl

                # TARGET HIT
                elif high >= target:
                    exit_price = target

                if exit_price:
                    pnl = (exit_price - entry) * qty

                    cur.execute("""
                    UPDATE trades
                    SET status='CLOSED', pnl=%s, exit_time=%s
                    WHERE id=%s
                    """, (pnl, datetime.now(), trade_id))

            except:
                continue

        self.conn.commit()
        cur.close()

    # ---------------- MAIN ---------------- #
    def run_once(self):
        signals = self.scan_market()
        trades = self.generate_trades(signals)

        self.save_trades(trades)
        self.update_trades()
