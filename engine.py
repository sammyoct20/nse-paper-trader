import yfinance as yf
import pandas as pd
from db import get_conn, create_tables
from datetime import datetime


class PaperEngine:

    def __init__(self):
        create_tables()

        self.swing_symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS"
        ]

    # ---------------- DATA ----------------
    def get_data(self, symbol, interval="1d", period="6mo"):
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)

            if df is None or df.empty:
                return None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            return df
        except:
            return None

    # ---------------- INDICATORS ----------------
    def apply_indicators(self, df):
        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()

        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df["ATR"] = (df["High"] - df["Low"]).rolling(14).mean()

        return df

    # ---------------- SWING SIGNAL ----------------
    def swing_signal(self, df):

        df = self.apply_indicators(df)
        last = df.iloc[-1]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        rsi = float(last["RSI"])
        atr = float(last["ATR"])

        if not (price > ema20 > ema50):
            return None

        if not (45 < rsi < 70):
            return None

        sl = price - (1.5 * atr)
        target = price + (price - sl) * 2

        return price, sl, target

    # ---------------- INTRADAY ORB ----------------
    def intraday_signal(self):

        df = self.get_data("^NSEI", interval="5m", period="5d")
        if df is None or len(df) < 20:
            return None

        orb = df.between_time("09:15", "09:30")
        if orb.empty:
            return None

        orb_high = orb["High"].max()
        orb_low = orb["Low"].min()

        latest = df.iloc[-1]
        price = float(latest["Close"])

        if price > orb_high:
            direction = "CALL"
            sl = orb_low
            target = price + (price - sl) * 2

        elif price < orb_low:
            direction = "PUT"
            sl = orb_high
            target = price - (sl - price) * 2

        else:
            return None

        return "NIFTY", direction, price, sl, target

    # ---------------- INSERT TRADE ----------------
    def insert_trade(self, symbol, entry, sl, target, trade_type, direction="BUY"):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT * FROM trades 
        WHERE symbol=? AND status='OPEN' AND type=?
        """, (symbol, trade_type))

        if cur.fetchone():
            conn.close()
            return

        cur.execute("""
        INSERT INTO trades 
        (symbol, entry, sl, target, status, entry_price, type, direction)
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """, (symbol, entry, sl, target, entry, trade_type, direction))

        conn.commit()
        conn.close()

    # ---------------- UPDATE TRADES ----------------
    def update_trades(self):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT id, symbol, entry_price, sl, target 
        FROM trades WHERE status='OPEN'
        """)

        trades = cur.fetchall()

        for t in trades:
            trade_id, symbol, entry_price, sl, target = t

            if symbol == "NIFTY":
                df = self.get_data("^NSEI", interval="5m", period="1d")
            else:
                df = self.get_data(symbol)

            if df is None:
                continue

            last = df.iloc[-1]
            high = float(last["High"])
            low = float(last["Low"])

            exit_price = None
            reason = None

            if low <= sl:
                exit_price = sl
                reason = "SL HIT"

            elif high >= target:
                exit_price = target
                reason = "TARGET HIT"

            if exit_price:
                pnl = exit_price - entry_price

                cur.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_price=?,
                    pnl=?,
                    exit_reason=?,
                    closed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """, (exit_price, pnl, reason, trade_id))

        conn.commit()
        conn.close()

    # ---------------- RUN ENGINE ----------------
    def run(self):

        self.update_trades()

        # -------- SWING --------
        for sym in self.swing_symbols:

            df = self.get_data(sym)
            if df is None:
                continue

            signal = self.swing_signal(df)

            if signal:
                entry, sl, target = signal
                self.insert_trade(sym, entry, sl, target, "SWING", "BUY")

        # -------- INTRADAY --------
        intraday = self.intraday_signal()

        if intraday:
            symbol, direction, entry, sl, target = intraday
            self.insert_trade(symbol, entry, sl, target, "INTRADAY", direction)

    def run_once(self):
        self.run()
