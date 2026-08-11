import yfinance as yf
import pandas as pd
import psycopg2
import os
from datetime import datetime
import time

class PaperEngine:

    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.conn = psycopg2.connect(self.db_url)
        self.capital = 200000
        self.max_trades = 3
        self.risk_per_trade = 0.02
        self.create_tables()

        # NIFTY 50 stocks
        self.symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","ICICIBANK.NS","HDFCBANK.NS",
            "LT.NS","SBIN.NS","AXISBANK.NS","KOTAKBANK.NS","ITC.NS",
            "HINDUNILVR.NS","BHARTIARTL.NS","ASIANPAINT.NS","MARUTI.NS",
            "BAJFINANCE.NS","BAJAJFINSV.NS","SUNPHARMA.NS","ULTRACEMCO.NS",
            "NESTLEIND.NS","TITAN.NS"
        ]

    # ---------------- DB ----------------
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
            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exit_time TIMESTAMP,
            exit_reason TEXT,
            pnl FLOAT
        );
        """)

        self.conn.commit()
        cur.close()

    # ---------------- DATA ----------------
    def get_data(self, symbol):
        try:
            df = yf.download(
                symbol,
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=True
            )
            if df.empty:
                return None

            df["EMA20"] = df["Close"].ewm(span=20).mean()
            df["EMA50"] = df["Close"].ewm(span=50).mean()

            return df

        except:
            return None

    # ---------------- STRATEGY ----------------
    def generate_signal(self, symbol):
        df = self.get_data(symbol)
        if df is None or len(df) < 50:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price = float(latest["Close"])
        ema20 = float(latest["EMA20"])
        ema50 = float(latest["EMA50"])
        volume = float(latest["Volume"])
        avg_volume = float(df["Volume"].tail(20).mean())

        # 🔴 Strong filters only
        if not (price > ema20 > ema50):
            return None

        if volume < 1.5 * avg_volume:
            return None

        if price < prev["High"]:
            return None

        return {
            "symbol": symbol,
            "entry": price
        }

    # ---------------- POSITION SIZING ----------------
    def position_size(self, entry):
        risk_amount = self.capital * self.risk_per_trade
        sl = entry * 0.99
        risk_per_share = entry - sl

        if risk_per_share <= 0:
            return 0

        qty = int(risk_amount / risk_per_share)
        return max(qty, 1)

    # ---------------- SAVE TRADE ----------------
    def save_trade(self, trade):
        cur = self.conn.cursor()

        cur.execute("""
        INSERT INTO trades (symbol, entry, stop_loss, target, qty, status)
        VALUES (%s, %s, %s, %s, %s, 'OPEN')
        """, (
            trade["symbol"],
            trade["entry"],
            trade["stop_loss"],
            trade["target"],
            trade["qty"]
        ))

        self.conn.commit()
        cur.close()

    # ---------------- FETCH OPEN TRADES ----------------
    def get_open_trades(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, symbol, entry, stop_loss, target, qty FROM trades WHERE status='OPEN'")
        rows = cur.fetchall()
        cur.close()
        return rows

    # ---------------- EXIT LOGIC ----------------
    def update_trades(self):
        trades = self.get_open_trades()
        cur = self.conn.cursor()

        for t in trades:
            trade_id, symbol, entry, sl, target, qty = t

            df = self.get_data(symbol)
            if df is None:
                continue

            price = float(df["Close"].iloc[-1])

            exit_reason = None

            if price <= sl:
                exit_reason = "STOP LOSS"

            elif price >= target:
                exit_reason = "TARGET HIT"

            # Time exit (after 2 days approx)
            cur.execute("SELECT entry_time FROM trades WHERE id=%s", (trade_id,))
            entry_time = cur.fetchone()[0]

            if (datetime.now() - entry_time).days >= 2:
                exit_reason = "TIME EXIT"

            if exit_reason:
                pnl = (price - entry) * qty

                cur.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_time=NOW(),
                    exit_reason=%s,
                    pnl=%s
                WHERE id=%s
                """, (exit_reason, pnl, trade_id))

        self.conn.commit()
        cur.close()

    # ---------------- MAIN RUN ----------------
    def run_once(self):

        self.update_trades()

        open_trades = self.get_open_trades()
        if len(open_trades) >= self.max_trades:
            return [], []

        signals = []
        trades = []

        for sym in self.symbols:
            signal = self.generate_signal(sym)

            if signal:
                entry = signal["entry"]
                sl = entry * 0.99
                target = entry * 1.02
                qty = self.position_size(entry)

                trade = {
                    "symbol": sym,
                    "entry": entry,
                    "stop_loss": sl,
                    "target": target,
                    "qty": qty
                }

                self.save_trade(trade)

                signals.append(signal)
                trades.append(trade)

                if len(self.get_open_trades()) >= self.max_trades:
                    break

            time.sleep(0.5)  # avoid rate limit

        return signals, trades
