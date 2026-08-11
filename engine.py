import yfinance as yf
import pandas as pd
import numpy as np


class SwingEngine:

    def __init__(self):

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
    def get_data(self, symbol):
        try:
            df = yf.download(symbol, period="6mo", interval="1d", progress=False)

            if df is None or df.empty or len(df) < 60:
                return None

            return df
        except:
            return None

    # ---------------- INDICATORS ----------------
    def apply_indicators(self, df):

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()

        # RSI
        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # ATR
        df["H-L"] = df["High"] - df["Low"]
        df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
        df["L-PC"] = abs(df["Low"] - df["Close"].shift(1))
        df["TR"] = df[["H-L","H-PC","L-PC"]].max(axis=1)
        df["ATR"] = df["TR"].rolling(14).mean()

        return df

    # ---------------- MARKET FILTER ----------------
    def check_market_trend(self):

        df = self.get_data("^NSEI")

        if df is None:
            return False

        df = self.apply_indicators(df)

        latest = df.iloc[-1]

        if latest["Close"] > latest["EMA20"] > latest["EMA50"]:
            return True

        return False

    # ---------------- SCORING ----------------
    def calculate_score(self, price, ema20, ema50, rsi, volume, avg_volume):

        score = 0

        # Trend strength
        trend_strength = (ema20 - ema50) / ema50
        score += trend_strength * 100

        # RSI sweet spot (closer to 55 is better)
        score += max(0, 15 - abs(rsi - 55))

        # Volume boost
        if volume > avg_volume:
            score += 10

        # Pullback quality (closer to EMA20 better)
        distance = abs(price - ema20) / ema20
        score += max(0, 10 - (distance * 100))

        return round(score, 2)

    # ---------------- STRATEGY ----------------
    def generate_signal(self, df):

        if df is None or len(df) < 60:
            return None

        df = self.apply_indicators(df)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price = float(latest["Close"])
        ema20 = float(latest["EMA20"])
        ema50 = float(latest["EMA50"])
        rsi = float(latest["RSI"])
        atr = float(latest["ATR"])
        volume = float(latest["Volume"])
        avg_volume = float(df["Volume"].tail(20).mean())

        # Filters
        if not (price > ema20 > ema50):
            return None

        if (price - ema20) / ema20 > 0.03:
            return None

        if not (50 < rsi < 65):
            return None

        if volume < avg_volume:
            return None

        if price <= float(prev["Close"]):
            return None

        # ATR SL
        sl = price - (1.5 * atr)
        sl_percent = (price - sl) / price

        if sl_percent > 0.03:
            return None

        risk = price - sl
        target = price + (2 * risk)

        rr = (target - price) / risk
        if rr < 2:
            return None

        # SCORE
        score = self.calculate_score(price, ema20, ema50, rsi, volume, avg_volume)

        return {
            "entry": round(price, 2),
            "stop_loss": round(sl, 2),
            "target": round(target, 2),
            "rsi": round(rsi, 2),
            "rr": round(rr, 2),
            "score": score
        }

    # ---------------- RUN ----------------
    def run(self):

        print("\nChecking market trend...\n")

        if not self.check_market_trend():
            print("❌ Market not favorable → No trades\n")
            return []

        print("✅ Market OK → Scanning...\n")

        results = []

        for sym in self.symbols:
            df = self.get_data(sym)
            signal = self.generate_signal(df)

            if signal:
                results.append({
                    "symbol": sym,
                    "data": signal
                })

        # SORT BY SCORE
        results = sorted(results, key=lambda x: x["data"]["score"], reverse=True)

        return results


# ---------------- MAIN ----------------
if __name__ == "__main__":

    engine = SwingEngine()
    output = engine.run()

    print("\n🔥 TOP SWING SETUPS:\n")

    if not output:
        print("No valid setups today")

    for stock in output[:5]:   # top 5 only
        print(f"""
Stock: {stock['symbol']}
Entry: {stock['data']['entry']}
SL: {stock['data']['stop_loss']}
Target: {stock['data']['target']}
RSI: {stock['data']['rsi']}
RR: {stock['data']['rr']}
Score: {stock['data']['score']}
--------------------------
""")
