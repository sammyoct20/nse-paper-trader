import yfinance as yf
import pandas as pd
import time


class PaperEngine:

    def __init__(self):

        # NIFTY 50 STOCKS
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

            if df is None or df.empty:
                return None

            # FIX: MultiIndex columns issue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if len(df) < 60:
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

        try:
            price = float(last["Close"])
            ema20 = float(last["EMA20"])
            ema50 = float(last["EMA50"])
        except:
            return False

        return price > ema20 > ema50

    # ---------------- SCORING ----------------
    def calculate_score(self, price, ema20, ema50, rsi, volume, avg_volume):

        score = 0

        # Trend strength
        trend = (ema20 - ema50) / ema50
        score += trend * 100

        # RSI sweet zone
        score += max(0, 15 - abs(rsi - 55))

        # Volume boost
        if volume > avg_volume:
            score += 10

        # Pullback quality
        distance = abs(price - ema20) / ema20
        score += max(0, 10 - (distance * 100))

        return round(score, 2)

    # ---------------- SIGNAL ----------------
    def generate_signal(self, df):

        df = self.apply_indicators(df)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        try:
            price = float(last["Close"])
            ema20 = float(last["EMA20"])
            ema50 = float(last["EMA50"])
            rsi = float(last["RSI"])
            atr = float(last["ATR"])
            volume = float(last["Volume"])
            avg_volume = float(df["Volume"].tail(20).mean())
        except:
            return None

        # Trend
        if not (price > ema20 > ema50):
            return None

        # Pullback
        if (price - ema20) / ema20 > 0.03:
            return None

        # RSI
        if not (50 < rsi < 65):
            return None

        # Volume
        if volume < avg_volume:
            return None

        # Confirmation
        if price <= float(prev["Close"]):
            return None

        # SL (ATR based)
        sl = price - (1.5 * atr)
        risk = price - sl

        if risk <= 0:
            return None

        # Max SL filter
        if (risk / price) > 0.03:
            return None

        # Target
        target = price + (2 * risk)

        # RR validation
        rr = (target - price) / risk
        if rr < 2:
            return None

        # Score
        score = self.calculate_score(price, ema20, ema50, rsi, volume, avg_volume)

        return {
            "symbol": "",
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "rr": round(rr, 2),
            "score": score
        }

    # ---------------- RUN ----------------
    def run(self):

        if not self.market_ok():
            print("❌ Market not favorable")
            return []

        results = []

        for sym in self.symbols:

            df = self.get_data(sym)

            if df is None:
                continue

            signal = self.generate_signal(df)

            if signal:
                signal["symbol"] = sym
                results.append(signal)

            time.sleep(0.3)  # prevent rate limit

        # Sort by score
        results = sorted(results, key=lambda x: x["score"], reverse=True)

        return results

    # Required for worker
    def run_once(self):
        return self.run()
