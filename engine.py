import yfinance as yf
import pandas as pd
import time


class PaperEngine:

    def __init__(self):

        # FULL NIFTY 50
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

        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df["TR"] = df["High"] - df["Low"]
        df["ATR"] = df["TR"].rolling(14).mean()

        return df

    # ---------------- MARKET FILTER ----------------
    def market_ok(self):

        df = self.get_data("^NSEI")

        if df is None:
            return False

        df = self.apply_indicators(df)
        last = df.iloc[-1]

        return last["Close"] > last["EMA20"] > last["EMA50"]

    # ---------------- SCORING ----------------
    def score(self, price, ema20, ema50, rsi, volume, avg_volume):

        score = 0

        trend = (ema20 - ema50) / ema50
        score += trend * 100

        score += max(0, 15 - abs(rsi - 55))

        if volume > avg_volume:
            score += 10

        distance = abs(price - ema20) / ema20
        score += max(0, 10 - (distance * 100))

        return round(score, 2)

    # ---------------- SIGNAL ----------------
    def generate_signal(self, df):

        df = self.apply_indicators(df)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])
        rsi = float(last["RSI"])
        atr = float(last["ATR"])
        volume = float(last["Volume"])
        avg_volume = float(df["Volume"].tail(20).mean())

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

        sl = price - (1.5 * atr)
        risk = price - sl

        if risk <= 0 or (risk / price) > 0.03:
            return None

        target = price + (2 * risk)

        rr = (target - price) / risk
        if rr < 2:
            return None

        score = self.score(price, ema20, ema50, rsi, volume, avg_volume)

        return {
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "rr": round(rr, 2),
            "score": score
        }

    # ---------------- RUN ----------------
    def run(self):

        if not self.market_ok():
            return []

        results = []

        for sym in self.symbols:

            df = self.get_data(sym)

            if df is None:
                continue

            signal = self.generate_signal(df)

            if signal:
                results.append({
                    "symbol": sym,
                    **signal
                })

            time.sleep(0.3)  # avoid rate limit

        results = sorted(results, key=lambda x: x["score"], reverse=True)

        return results

    def run_once(self):
        return self.run()
