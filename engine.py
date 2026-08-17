import yfinance as yf
import pandas as pd
import time
from db import get_conn, create_tables


class PaperEngine:

    def __init__(self):
        create_tables()

        self.symbols = [
            "ADANIENT.NS","ADANIPORTS.NS","APOLLOHOSP.NS","ASIANPAINT.NS","AXISBANK.NS",
            "BAJAJ-AUTO.NS","BAJFINANCE.NS","BAJAJFINSV.NS","BPCL.NS","BHARTIARTL.NS",
            "BRITANNIA.NS","CIPLA.NS","COALINDIA.NS","DIVISLAB.NS","DRREDDY.NS",
            "EICHERMOT.NS","GRASIM.NS","HCLTECH.NS","HDFCBANK.NS","HDFCLIFE.NS",
            "HEROMOTOCO.NS","HINDALCO.NS","HINDUNILVR.NS","ICICIBANK.NS","ITC.NS",
            "INDUSINDBK.NS","INFY.NS","JSWSTEEL.NS","KOTAKBANK.NS","LT.NS",
            "M&M.NS","MARUTI.NS","NTPC.NS","NESTLEIND.NS","ONGC.NS",
            "POWERGRID.NS","RELIANCE.NS","SBILIFE.NS","SBIN.NS","SUNPHARMA.NS",
            "TCS.NS","TATACONSUM.NS","TATAMOTORS.NS","TATASTEEL.NS","TECHM.NS",
            "TITAN.NS","ULTRACEMCO.NS","UPL.NS","WIPRO.NS"
        ]

    # ---------------- DATA ----------------
    def get_data(self, symbol, interval="1d"):
        try:
            df = yf.download(symbol, period="6mo", interval=interval, progress=False)

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

    # ---------------- MARKET FILTER ----------------
    def market_ok(self):
        df = self.get_data("^NSEI")
        if df is None:
            return False

        df = self.apply_indicators(df)
        last = df.iloc[-1]

        try:
            return float(last["Close"]) > float(last["EMA20"])
        except:
            return False

    # ---------------- RELATIVE STRENGTH (RELAXED) ----------------
    def relative_strength(self, df_stock, df_nifty):
        try:
            stock_ret = (df_stock["Close"].iloc[-20] - df_stock["Close"].iloc[-1]) * -1
            nifty_ret = (df_nifty["Close"].iloc[-20] - df_nifty["Close"].iloc[-1]) * -1

            return stock_ret > (nifty_ret - 0.02)
        except:
            return False

    # ---------------- WEEKLY TREND (SOFT FILTER) ----------------
    def weekly_trend_ok(self, symbol):
        df = self.get_data(symbol, interval="1wk")
        if df is None:
            return True   # allow if data missing

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        last = df.iloc[-1]

        try:
            return float(last["Close"]) > float(last["EMA20"])
        except:
            return True

    # ---------------- SIGNAL ----------------
    def generate_signal(self, df):

        df = self.apply_indicators(df)

        last = df.iloc[-1]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        rsi = float(last["RSI"])
        atr = float(last["ATR"])

        # TREND
        if not (price > ema20 > ema50):
            return None

        # RELAXED RSI
        if not (45 < rsi < 70):
            return None

        # REMOVE STRICT MOMENTUM BLOCK
        if price < ema20:
            return None

        # SL + RR
        sl = price - (1.5 * atr)
        risk = price - sl

        if risk <= 0:
            return None

        target = price + (2 * risk)

        return price, sl, target

    # ---------------- INSERT TRADE ----------------
    def insert_trade(self, symbol, entry, sl, target):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trades WHERE symbol=? AND status='OPEN'", (symbol,))
        if cur.fetchone():
            conn.close()
            return

        cur.execute("""
        INSERT INTO trades (symbol, entry, sl, target, status, entry_price)
        VALUES (?, ?, ?, ?, 'OPEN', ?)
        """, (symbol, entry, sl, target, entry))

        conn.commit()
        conn.close()

    # ---------------- UPDATE TRADES ----------------
    def update_trades(self):

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT id, symbol, entry_price, sl, target FROM trades WHERE status='OPEN'")
        trades = cur.fetchall()

        for t in trades:
            trade_id, symbol, entry_price, sl, target = t

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

    # ---------------- RUN ----------------
    def run(self):

        self.update_trades()

        if not self.market_ok():
            print("Market weak")
            return []

        df_nifty = self.get_data("^NSEI")
        results = []

        for sym in self.symbols:

            df = self.get_data(sym)
            if df is None:
                continue

            if not self.relative_strength(df, df_nifty):
                continue

            # SOFT weekly filter (not blocking)
            if not self.weekly_trend_ok(sym):
                pass

            signal = self.generate_signal(df)

            if signal:
                entry, sl, target = signal
                self.insert_trade(sym, entry, sl, target)

                results.append({
                    "symbol": sym,
                    "entry": round(entry, 2),
                    "sl": round(sl, 2),
                    "target": round(target, 2)
                })

            time.sleep(0.2)

        return results

    def run_once(self):
        return self.run()
