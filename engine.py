import yfinance as yf
import pandas as pd
from db import get_conn, create_tables


class PaperEngine:

    def __init__(self):
        create_tables()

        self.swing_symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS"
        ]

    # ---------------- DATA ----------------
    def get_data(self, symbol, interval="1d", period="6mo"):
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True
            )

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

    # ---------------- SWING ----------------
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

    # ---------------- INTRADAY ----------------
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
            return "NIFTY", "CALL", price, orb_low, price + (price - orb_low) * 2

        elif price < orb_low:
            return "NIFTY", "PUT", price, orb_high, price - (orb_high - price) * 2

        return None

    # ---------------- INSERT ----------------
    def insert_trade(self, symbol, entry, sl, target, ttype, direction):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT * FROM trades 
        WHERE symbol=? AND status='OPEN' AND type=?
        """, (symbol, ttype))

        if cur.fetchone():
            conn.close()
            return

        cur.execute("""
        INSERT INTO trades 
        (symbol, entry, sl, target, status, entry_price, type, direction)
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """, (symbol, entry, sl, target, entry, ttype, direction))

        conn.commit()
        conn.close()

    # ---------------- UPDATE ----------------
    def update_trades(self):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT id, symbol, entry_price, sl, target 
        FROM trades WHERE status='OPEN'
        """)

        for trade in cur.fetchall():
            trade_id, symbol, entry, sl, target = trade

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
                pnl = exit_price - entry

                cur.execute("""
                UPDATE trades SET
                    status='CLOSED',
                    exit_price=?,
                    pnl=?,
                    exit_reason=?,
                    closed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """, (exit_price, pnl, reason, trade_id))

        conn.commit()
        conn.close()

    # ---------------- RUN ----------------
    def run(self):

        self.update_trades()

        # Swing
        for sym in self.swing_symbols:
            df = self.get_data(sym)
            if df is None:
                continue

            sig = self.swing_signal(df)
            if sig:
                entry, sl, target = sig
                self.insert_trade(sym, entry, sl, target, "SWING", "BUY")

        # Intraday
        intraday = self.intraday_signal()
        if intraday:
            symbol, direction, entry, sl, target = intraday
            self.insert_trade(symbol, entry, sl, target, "INTRADAY", direction)

    def run_once(self):
        self.run()
