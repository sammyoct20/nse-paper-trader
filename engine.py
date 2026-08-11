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

    # ---------------- NIFTY 50 ---------------- #
    def get_nifty50(self):
        return [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
            "SBIN.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS","LT.NS",
            "HCLTECH.NS","AXISBANK.NS","ASIANPAINT.NS","MARUTI.NS","SUNPHARMA.NS",
            "ULTRACEMCO.NS","TITAN.NS","BAJFINANCE.NS","BAJAJFINSV.NS","WIPRO.NS",
            "NESTLEIND.NS","HINDUNILVR.NS","POWERGRID.NS","NTPC.NS","ONGC.NS",
            "COALINDIA.NS","TATASTEEL.NS","JSWSTEEL.NS","GRASIM.NS","ADANIENT.NS",
            "ADANIPORTS.NS","APOLLOHOSP.NS","BRITANNIA.NS","CIPLA.NS","DIVISLAB.NS",
            "DRREDDY.NS","EICHERMOT.NS","HEROMOTOCO.NS","INDUSINDBK.NS","BAJAJ-AUTO.NS",
            "M&M.NS","SHRIRAMFIN.NS","TATACONSUM.NS","TECHM.NS","UPL.NS",
            "HDFCLIFE.NS","SBILIFE.NS","ICICIPRULI.NS","HAVELLS.NS","PIDILITIND.NS"
        ]

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

        df["TR"] = df[["High","Low","Close"]].apply(
            lambda x: max(x["High"]-x["Low"],
                          abs(x["High"]-x["Close"]),
                          abs(x["Low"]-x["Close"])),
            axis=1
        )
        df["ATR"] = df["TR"].rolling(14).mean()

        return df

    # ---------------- MARKET FILTER ---------------- #
    def is_market_bullish(self):
        df = yf.download("^NSEI", period="5d", interval="15m",
                         auto_adjust=False, progress=False)

        if df.empty:
            return False

        df["EMA50"] = df["Close"].ewm(span=50).mean()

        return float(df["Close"].iloc[-1].item()) > float(df["EMA50"].iloc[-1].item())

    # ---------------- SCAN ---------------- #
    def scan_market(self):

        symbols = self.get_nifty50()

        if not self.is_market_bullish():
            print("Market not bullish")
            return []

        df_all = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="15m",
            group_by="ticker",
            auto_adjust=False,
            threads=True
        )

        signals = []

        for sym in symbols:
            try:
                df = df_all[sym]

                if df.empty or len(df) < 50:
                    continue

                df = self.add_indicators(df)
                last = df.iloc[-1]

                trend = last["Close"] > last["EMA50"] > last["EMA200"]
                momentum = 55 < last["RSI"] < 70
                breakout = last["Close"] > df["HH"].iloc[-2] * 0.995
                volume = last["Volume"] > 1.2 * last["VOL_AVG"]

                if trend and momentum and breakout and volume:
                    signals.append({
                        "symbol": sym,
                        "price": float(last["Close"].item()),
                        "atr": float(last["ATR"].item())
                    })

            except Exception as e:
                print(sym, e)

        return signals

    # ---------------- TRADE ---------------- #
    def generate_trades(self, signals):

        trades = []

        for s in signals:
            entry = s["price"]
            atr = s["atr"]

            sl = entry - (1.5 * atr)
            target = entry + (2 * atr)

            risk = entry - sl
            if risk <= 0:
                continue

            qty = int((self.capital * self.risk_per_trade) / risk)

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
                df = yf.download(sym, period="1d", interval="5m",
                                 auto_adjust=False, progress=False)

                if df.empty:
                    continue

                price = float(df["Close"].iloc[-1].item())

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

    # ---------------- WORKER SUPPORT ---------------- #
    def run_once(self):
        print("=== ENGINE START ===")

        signals = self.scan_market()
        trades = self.generate_trades(signals)

        self.save_trades(trades)
        self.update_trades()

        print("=== ENGINE END ===")
