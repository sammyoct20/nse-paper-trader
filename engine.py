import yfinance as yf
import pandas as pd
import numpy as np
from db import get_conn, create_tables

class PaperEngine:

    def __init__(self):
        create_tables()
        self.swing_symbols = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "TATAMOTORS.NS"
        ]

    # ---------------- DATA RETRIEVAL ----------------
    def get_data(self, symbol, interval="1d", period="1y"):
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
        except Exception:
            return None

    def format_symbol(self, symbol):
        symbol = symbol.upper().strip()
        if symbol.endswith(".NS") or symbol.endswith(".BO") or symbol == "^NSEI":
            return symbol
        return symbol + ".NS"

    # ---------------- INDICATOR CALCULATIONS ----------------
    def apply_indicators(self, df):
        df = df.copy()

        # Moving Averages
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

        # RSI
        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss.replace(0, np.nan))
        df["RSI"] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

        # ATR (Average True Range)
        high_low = df["High"] - df["Low"]
        high_cp = (df["High"] - df["Close"].shift(1)).abs()
        low_cp = (df["Low"] - df["Close"].shift(1)).abs()
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        df["ATR"] = tr.rolling(14).mean()

        # ADX (Average Directional Index)
        up_move = df["High"].diff()
        down_move = df["Low"].shift(1) - df["Low"]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr_smooth = tr.rolling(14).sum()
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr_smooth)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr_smooth)

        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        df["ADX"] = dx.rolling(14).mean()

        # Volume Metrics
        if "Volume" in df.columns:
            df["vol_sma20"] = df["Volume"].rolling(20).mean()
            df["vol_multiplier"] = df["Volume"] / df["vol_sma20"].replace(0, np.nan)
        else:
            df["vol_sma20"] = 0
            df["vol_multiplier"] = 0.0

        return df

    def validate_df(self, df):
        required = ["Open", "High", "Low", "Close"]
        return df is not None and not df.empty and all(c in df.columns for c in required)

    def get_market_trend(self):
        nifty_df = self.get_data("^NSEI", interval="1d", period="6mo")
        if nifty_df is None or len(nifty_df) < 50:
            return "NEUTRAL"
        nifty_df = self.apply_indicators(nifty_df)
        last = nifty_df.iloc[-1]
        if last["Close"] > last["EMA50"]:
            return "BULLISH"
        elif last["Close"] < last["EMA50"]:
            return "BEARISH"
        return "NEUTRAL"

    # ---------------- SIGNALS ----------------
    def swing_signal(self, df):
        df = self.apply_indicators(df)
        if len(df) < 200:
            return None

        last = df.iloc[-1]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        ema200 = float(last["EMA200"])
        rsi = float(last["RSI"]) if not pd.isna(last["RSI"]) else 0
        adx = float(last["ADX"]) if not pd.isna(last["ADX"]) else 0
        atr = float(last["ATR"]) if not pd.isna(last["ATR"]) else 0
        vol_mult = float(last["vol_multiplier"]) if not pd.isna(last["vol_multiplier"]) else 0
        macd_hist = float(last["MACD_hist"]) if not pd.isna(last["MACD_hist"]) else 0

        if atr == 0:
            return None

        # Enhanced Institutional Entry Conditions
        macro_uptrend = price > ema200
        short_momentum = price > ema20 > ema50
        trend_strong = adx > 20
        rsi_healthy = 48 < rsi < 68
        volume_confirmed = vol_mult >= 1.2
        macd_positive = macd_hist > 0

        if macro_uptrend and short_momentum and trend_strong and rsi_healthy and volume_confirmed and macd_positive:
            sl = price - (1.5 * atr)
            target = price + ((price - sl) * 2.0)
            return price, sl, target, atr, adx, vol_mult

        return None

    def intraday_signal(self):
        df = self.get_data("^NSEI", interval="5m", period="5d")
        if df is None or len(df) < 30:
            return None

        df = self.apply_indicators(df)
        orb = df.between_time("09:15", "09:30")
        if orb.empty:
            return None

        orb_high = orb["High"].max()
        orb_low = orb["Low"].min()

        last = df.iloc[-1]
        price = float(last["Close"])
        vwap = (df["Volume"] * (df["High"] + df["Low"] + df["Close"]) / 3).sum() / df["Volume"].sum() if "Volume" in df.columns else price

        if price > orb_high and price > vwap:
            sl = orb_low
            target = price + (price - sl) * 2.0
            return "NIFTY", "CALL", price, sl, target
        elif price < orb_low and price < vwap:
            sl = orb_high
            target = price - (sl - price) * 2.0
            return "NIFTY", "PUT", price, sl, target

        return None

    # ---------------- DB OPERATIONS ----------------
    def insert_trade(self, symbol, entry, sl, target, ttype, direction, atr=0, adx=0, vol_ratio=0):
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trades WHERE symbol=? AND status='OPEN' AND type=?", (symbol, ttype))
        if cur.fetchone():
            conn.close()
            return

        cur.execute("""
        INSERT INTO trades 
        (symbol, entry, sl, target, status, entry_price, type, direction, atr, adx, volume_ratio)
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
        """, (symbol, entry, sl, target, entry, ttype, direction, atr, adx, vol_ratio))

        conn.commit()
        conn.close()

    def update_trades(self):
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT id, symbol, entry_price, sl, target FROM trades WHERE status='OPEN'")
        for trade in cur.fetchall():
            trade_id, symbol, entry, sl, target = trade
            df = self.get_data("^NSEI" if symbol == "NIFTY" else symbol)

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
                pnl = (exit_price - entry) if target > entry else (entry - exit_price)
                cur.execute("""
                UPDATE trades SET status='CLOSED', exit_price=?, pnl=?, exit_reason=?, closed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """, (exit_price, pnl, reason, trade_id))

        conn.commit()
        conn.close()

    # ---------------- ENHANCED ANALYZER ----------------
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
        ema200 = float(last["EMA200"])
        rsi = float(last["RSI"]) if not pd.isna(last["RSI"]) else 0
        adx = float(last["ADX"]) if not pd.isna(last["ADX"]) else 0
        atr = float(last["ATR"]) if not pd.isna(last["ATR"]) else 0
        macd_hist = float(last["MACD_hist"]) if not pd.isna(last["MACD_hist"]) else 0
        vol_mult = float(last["vol_multiplier"]) if not pd.isna(last["vol_multiplier"]) else 0

        resistance = df["High"].rolling(20).max().iloc[-1]
        support = df["Low"].rolling(20).min().iloc[-1]

        breakout = price > resistance
        breakout_strength = "STRONG" if breakout and vol_mult > 1.5 else ("WEAK" if breakout else "NONE")
        market_trend = self.get_market_trend()

        action = "HOLD"
        if price > ema200 and price > ema20 > ema50 and rsi > 50 and adx > 20 and vol_mult >= 1.3 and macd_hist > 0:
            action = "STRONG BUY"
        elif price > ema20 > ema50 and rsi > 45:
            action = "BUY"
        elif price < ema50 or rsi < 35:
            action = "EXIT"

        return {
            "symbol": symbol.replace(".NS", "").replace(".BO", ""),
            "price": round(price, 2),
            "RSI": round(rsi, 2),
            "ADX": round(adx, 2),
            "ATR": round(atr, 2),
            "EMA20": round(ema20, 2),
            "EMA50": round(ema50, 2),
            "EMA200": round(ema200, 2),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "volume_multiplier": f"{round(vol_mult, 2)}x",
            "breakout": breakout,
            "breakout_strength": breakout_strength,
            "market_trend": market_trend,
            "action": action
        }

    # ---------------- RUNNER ----------------
    def run(self):
        self.update_trades()

        for sym in self.swing_symbols:
            df = self.get_data(sym)
            if df is None:
                continue

            sig = self.swing_signal(df)
            if sig:
                entry, sl, target, atr, adx, vol_mult = sig
                self.insert_trade(sym, entry, sl, target, "SWING", "BUY", atr, adx, vol_mult)

        intraday = self.intraday_signal()
        if intraday:
            symbol, direction, entry, sl, target = intraday
            self.insert_trade(symbol, entry, sl, target, "INTRADAY", direction)

    def run_once(self):
        self.run()
