import yfinance as yf
import psycopg2
import os
from datetime import datetime

class PaperEngine:

    def __init__(self):
        self.conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        self.capital = 100000
        self.max_trades = 3

        # Nifty stocks (you can expand later)
        self.symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
            "LT.NS","SBIN.NS","ITC.NS","AXISBANK.NS","KOTAKBANK.NS"
        ]

        self.ensure_schema()

    # ----------------------------------
    # AUTO FIX DB SCHEMA (CRITICAL)
    # ----------------------------------
    def ensure_schema(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            entry FLOAT,
            stop_loss FLOAT,
            target FLOAT,
            qty INT,
            status TEXT DEFAULT 'OPEN',
            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exit_time TIMESTAMP,
            exit_reason TEXT,
            pnl FLOAT
        );
        """)

        # Ensure missing columns (safe updates)
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN';")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS target FLOAT;")

        self.conn.commit()
        cur.close()

    # ----------------------------------
    # FETCH DATA
    # ----------------------------------
    def get_data(self, symbol):
        try:
            df = yf.download(symbol, period="5d", interval="5m", progress=False)

            if df.empty or len(df) < 20:
                return None

            return df

        except:
            return None

    # ----------------------------------
    # STRATEGY (STRONG FILTER)
    # ----------------------------------
    def generate_signal(self, sym):
        df = self.get_data(sym)

        if df is None:
            return None

        df["ema20"] = df["Close"].ewm(span=20).mean()
        df["ema50"] = df["Close"].ewm(span=50).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # FIX: convert to scalar (avoid pandas error)
        price = float(latest["Close"])
        ema20 = float(latest["ema20"])
        ema50 = float(latest["ema50"])
        volume = float(latest["Volume"])
        avg_volume = float(df["Volume"].tail(20).mean())

        # STRONG FILTERS
        if not (price > ema20 and ema20 > ema50):
            return None

        if volume < 1.5 * avg_volume:
            return None

        if price <= float(prev["High"]):
            return None

        return {
            "symbol": sym,
            "entry": price,
            "stop_loss": price * 0.99,
            "target": price * 1.02,
            "qty": int(self.capital / price / self.max_trades)
        }

    # ----------------------------------
    # SAVE TRADE
    # ----------------------------------
    def save_trade(self, trade):
        cur = self.conn.cursor()

        cur.execute("""
        INSERT INTO trades (symbol, entry, stop_loss, target, qty, status, entry_time)
        VALUES (%s,%s,%s,%s,%s,'OPEN',CURRENT_TIMESTAMP)
        """, (
            trade["symbol"],
            trade["entry"],
            trade["stop_loss"],
            trade["target"],
            trade["qty"]
        ))

        self.conn.commit()
        cur.close()

    # ----------------------------------
    # GET OPEN TRADES
    # ----------------------------------
    def get_open_trades(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, symbol, entry, stop_loss, target, qty FROM trades WHERE status='OPEN'")
        rows = cur.fetchall()
        cur.close()
        return rows

    # ----------------------------------
    # UPDATE TRADES (AUTO CLOSE)
    # ----------------------------------
    def update_trades(self):
        trades = self.get_open_trades()
        cur = self.conn.cursor()

        for t in trades:
            trade_id, sym, entry, sl, target, qty = t

            df = yf.download(sym, period="1d", interval="5m", progress=False)

            if df.empty:
                continue

            price = float(df["Close"].iloc[-1])

            exit_reason = None

            if price <= sl:
                exit_reason = "STOP LOSS"

            elif price >= target:
                exit_reason = "TARGET HIT"

            if exit_reason:
                pnl = (price - entry) * qty

                print(f"Closing {sym} | {exit_reason}")

                cur.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_time=CURRENT_TIMESTAMP,
                    exit_reason=%s,
                    pnl=%s
                WHERE id=%s
                """, (exit_reason, pnl, trade_id))

        self.conn.commit()
        cur.close()

    # ----------------------------------
    # MAIN RUN
    # ----------------------------------
    def run_once(self):

        self.update_trades()

        open_trades = self.get_open_trades()
        slots = self.max_trades - len(open_trades)

        if slots <= 0:
            return [], 0

        signals = []
        trades_created = 0

        for sym in self.symbols:
            signal = self.generate_signal(sym)

            if signal:
                self.save_trade(signal)
                signals.append(signal)
                trades_created += 1
                slots -= 1

            if slots == 0:
                break

        return signals, trades_created
