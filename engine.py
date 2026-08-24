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

    def format_symbol(self, symbol):
        symbol = symbol.upper().strip()

        if symbol.endswith(".NS") or symbol.endswith(".BO"):
            return symbol

        return symbol + ".NS"

    # ---------------- INDICATORS ----------------
    def apply_indicators(self, df):
        df = df.copy()

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()

        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()

        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df["ATR"] = (df["High"] - df["Low"]).rolling(14).mean()

        # SAFE volume handling
        if "Volume" in df.columns:
            df["vol_avg"] = df["Volume"].rolling(20).mean()
        else:
            df["vol_avg"] = None

        return df

    # ---------------- VALIDATION ----------------
    def validate_df(self, df):
        required = ["Open", "High", "Low", "Close"]
        return df is not None and not df.empty and all(c in df.columns for c in required)

    # ---------------- SWING ----------------
    def swing_signal(self, df):
        df = self.apply_indicators(df)

        if len(df) < 60:
            return None

        last = df.iloc[-1]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        rsi = float(last["RSI"]) if not pd.isna(last["RSI"]) else 0
        atr = float(last["ATR"]) if not pd.isna(last["ATR"]) else 0

        if atr == 0:
            return None

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

        last = df.iloc[-1]
        price = float(last["Close"])

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

            if df is None or df.empty:
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

    # ---------------- ANALYZER ----------------
    def analyze_stock(self, symbol):

        symbol = self.format_symbol(symbol)
        df = self.get_data(symbol)

        if df is None or df.empty:
            symbol = symbol.replace(".NS", ".BO")
            df = self.get_data(symbol)

        if not self.validate_df(df) or len(df) < 100:
            return {"error": "Invalid or insufficient data"}

        df = self.apply_indicators(df)
        last = df.iloc[-1]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        rsi = float(last["RSI"]) if not pd.isna(last["RSI"]) else 0

        df["vol_avg"] = df["Volume"].rolling(20).mean() if "Volume" in df.columns else 0

        vol = float(last["Volume"]) if "Volume" in df.columns else 0
        vol_avg = float(df["vol_avg"].iloc[-1]) if "Volume" in df.columns else 0

        volume_strength = "HIGH" if vol_avg and vol > 1.5 * vol_avg else "LOW"

        resistance = df["High"].rolling(20).max().iloc[-1]
        support = df["Low"].rolling(20).min().iloc[-1]

        breakout = price > resistance
        breakout_strength = "STRONG" if breakout and (price - resistance)/price > 0.02 else "WEAK"

        action = "HOLD"

        if price > ema20 > ema50 and rsi > 50 and breakout and volume_strength == "HIGH":
            action = "STRONG BUY"
        elif price > ema20 > ema50:
            action = "BUY"
        elif price < ema50:
            action = "EXIT"

        return {
            "symbol": symbol.replace(".NS","").replace(".BO",""),
            "price": round(price,2),
            "RSI": round(rsi,2),
            "EMA20": round(ema20,2),
            "EMA50": round(ema50,2),
            "support": round(support,2),
            "resistance": round(resistance,2),
            "volume_strength": volume_strength,
            "breakout": breakout,
            "breakout_strength": breakout_strength,
            "action": action
        }

    # ---------------- RUN ----------------
    def run(self):

        self.update_trades()

        for sym in self.swing_symbols:
            df = self.get_data(sym)
            if df is None:
                continue

            sig = self.swing_signal(df)
            if sig:
                entry, sl, target = sig
                self.insert_trade(sym, entry, sl, target, "SWING", "BUY")

        intraday = self.intraday_signal()
        if intraday:
            symbol, direction, entry, sl, target = intraday
            self.insert_trade(symbol, entry, sl, target, "INTRADAY", direction)

    def run_once(self):
        self.run()
