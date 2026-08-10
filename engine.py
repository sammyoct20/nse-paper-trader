import yfinance as yf
import psycopg2
import os
import time
from datetime import datetime


class PaperEngine:

    def __init__(self):
        self.capital = 100000
        self.risk_per_trade = 0.01
        self.conn = psycopg2.connect(os.environ["DATABASE_URL"])
        self.create_table()

    def create_table(self):
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
            entry_time TIMESTAMP,
            exit_price FLOAT,
            pnl FLOAT
        )
        """)
        self.conn.commit()

    # ---------- HELPERS ----------
    def get_open_trades(self):
        cur = self.conn.cursor()
        cur.execute("SELECT symbol FROM trades WHERE status='OPEN'")
        return [r[0] for r in cur.fetchall()]

    # ---------- SAVE ----------
    def save_trade(self, t):
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO trades (symbol, entry, stop_loss, target, qty, status, entry_time)
        VALUES (%s,%s,%s,%s,%s,'OPEN',%s)
        """, (
            t["symbol"], t["entry"], t["stop_loss"],
            t["target"], t["qty"], datetime.now()
        ))
        self.conn.commit()

    # ---------- UPDATE ----------
    def update_trades(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id,symbol,entry,stop_loss,target,qty FROM trades WHERE status='OPEN'")
        rows = cur.fetchall()

        for r in rows:
            trade_id, sym, entry, sl, target, qty = r

            try:
                df = yf.download(sym, period="1d", interval="5m", progress=False)
                if df.empty:
                    continue

                price = float(df["Close"].iloc[-1])

                if price <= sl:
                    pnl = (price - entry) * qty
                    cur.execute("UPDATE trades SET status='SL HIT', exit_price=%s, pnl=%s WHERE id=%s",
                                (price, pnl, trade_id))

                elif price >= target:
                    pnl = (price - entry) * qty
                    cur.execute("UPDATE trades SET status='TARGET HIT', exit_price=%s, pnl=%s WHERE id=%s",
                                (price, pnl, trade_id))

            except Exception as e:
                print("Update error:", e)

        self.conn.commit()

    # ---------- SCANNER ----------
    def scan_market(self):

        symbols = ["TCS.NS", "INFY.NS", "TITAN.NS", "HDFCBANK.NS"]

        signals = []

        for sym in symbols:
            try:
                df = yf.download(sym, period="5d", interval="5m", progress=False)

                if df.empty or len(df) < 50:
                    continue

                close = float(df["Close"].iloc[-1])
                sl = float(df["Low"].rolling(5).min().iloc[-1])
                risk = close - sl

                if risk <= 0:
                    continue

                target = close + 2 * risk
                qty = int((self.capital * self.risk_per_trade) / risk)

                if qty <= 0:
                    continue

                signals.append({
                    "symbol": sym,
                    "entry": round(close, 2),
                    "stop_loss": round(sl, 2),
                    "target": round(target, 2),
                    "qty": qty
                })

            except Exception as e:
                print("Scan error:", e)

        return signals[:3]

    # ---------- LOOP ----------
    def run_loop(self):
        print("🚀 Scanner started...")

        while True:
            try:
                print("\nCycle:", datetime.now())

                self.update_trades()

                open_trades = self.get_open_trades()
                MAX_TRADES = 3

                if len(open_trades) < MAX_TRADES:

                    signals = self.scan_market()

                    for s in signals:

                        if s["symbol"] in open_trades:
                            continue

                        if len(open_trades) >= MAX_TRADES:
                            break

                        self.save_trade(s)
                        open_trades.append(s["symbol"])

                else:
                    print("Max trades reached")

            except Exception as e:
                print("Loop error:", e)

            time.sleep(300)
