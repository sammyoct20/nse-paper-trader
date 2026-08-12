import yfinance as yf
import pandas as pd
import time


class PaperEngine:

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

        # Simple sector grouping
        self.sectors = {
            "BANK": ["HDFCBANK.NS","ICICIBANK.NS","SBIN.NS","KOTAKBANK.NS","AXISBANK.NS"],
            "IT": ["INFY.NS","TCS.NS","WIPRO.NS","HCLTECH.NS","TECHM.NS"],
            "AUTO": ["MARUTI.NS","TATAMOTORS.NS","M&M.NS","BAJAJ-AUTO.NS","EICHERMOT.NS"],
            "FMCG": ["ITC.NS","HINDUNILVR.NS","NESTLEIND.NS","BRITANNIA.NS","TATACONSUM.NS"]
        }

    # ---------------- DATA ----------------
    def get_data(self, symbol, interval="1d"):
        try:
            df = yf.download(symbol, period="6mo", interval=interval, progress=False)

            if df is None or df.empty:
                return None

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if len(df) < 50:
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

    # ---------------- RELATIVE STRENGTH ----------------
    def relative_strength(self, df_stock, df_nifty):

        try:
            stock_ret = (df_stock["Close"].iloc[-1] / df_stock["Close"].iloc[-20]) - 1
            nifty_ret = (df_nifty["Close"].iloc[-1] / df_nifty["Close"].iloc[-20]) - 1

            return stock_ret > nifty_ret
        except:
            return False

    # ---------------- WEEKLY TREND ----------------
    def weekly_trend_ok(self, symbol):

        df = self.get_data(symbol, interval="1wk")
        if df is None:
            return False

        df["EMA20"] = df["Close"].ewm(span=20).mean()

        last = df.iloc[-1]

        try:
            return float(last["Close"]) > float(last["EMA20"])
        except:
            return False

    # ---------------- SECTOR STRENGTH ----------------
    def sector_ok(self, symbol, df_nifty):

        for sector, stocks in self.sectors.items():

            if symbol in stocks:

                returns = []

                for s in stocks:
                    df = self.get_data(s)
                    if df is None:
                        continue

                    r = (df["Close"].iloc[-1] / df["Close"].iloc[-20]) - 1
                    returns.append(r)

                if not returns:
                    return False

                sector_avg = sum(returns) / len(returns)

                nifty_ret = (df_nifty["Close"].iloc[-1] / df_nifty["Close"].iloc[-20]) - 1

                return sector_avg > nifty_ret

        return True  # if not mapped, allow

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
        except:
            return None

        # ORIGINAL LOGIC (unchanged)
        if not (price > ema20 > ema50):
            return None

        if not (50 < rsi < 65):
            return None

        if price <= float(prev["Close"]):
            return None

        sl = price - (1.5 * atr)
        risk = price - sl

        if risk <= 0 or (risk / price) > 0.03:
            return None

        target = price + (2 * risk)

        return {
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "target": round(target, 2)
        }

    # ---------------- RUN ----------------
    def run(self):

        if not self.market_ok():
            print("❌ Market weak")
            return []

        df_nifty = self.get_data("^NSEI")
        results = []

        for sym in self.symbols:

            df = self.get_data(sym)
            if df is None:
                continue

            # 🔥 NEW FILTERS (layered)
            if not self.relative_strength(df, df_nifty):
                continue

            if not self.weekly_trend_ok(sym):
                continue

            if not self.sector_ok(sym, df_nifty):
                continue

            # ORIGINAL SIGNAL
            signal = self.generate_signal(df)

            if signal:
                results.append({
                    "symbol": sym,
                    **signal
                })

            time.sleep(0.3)

        return results

    def run_once(self):
        return self.run()
