import io
import warnings
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import ta

warnings.filterwarnings("ignore")

class PaperEngine:
    """
    High-performance paper trading & scanner engine supporting Swing, Intraday,
    BTST setups, and single-stock diagnostics for NSE equities.
    """
    def __init__(self, initial_balance=100000.0, risk_per_trade_pct=1.0):
        self.balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct

    def fetch_nse_universe(self, index_name="NIFTY 500"):
        """Fetches ticker lists dynamically from NSE archives."""
        urls = {
            "NIFTY 50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
            "NIFTY NEXT 50": "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
            "NIFTY 500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        }
        url = urls.get(index_name, urls["NIFTY 500"])
        headers = {"User-Agent": "Mozilla/5.0"}
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            df = pd.read_csv(io.StringIO(res.text))
            return [f"{symbol}.NS" for symbol in df['Symbol'].tolist()]
        except Exception:
            # High liquidity fallback list
            return [
                "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
                "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "AXISBANK.NS"
            ]

    def scan_all_strategies(self, tickers=None):
        """
        Single-pass scanner evaluating Swing, Intraday, and BTST criteria simultaneously.
        """
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 500")
            
        risk_amount = self.balance * (self.risk_per_trade_pct / 100.0)
        
        # Batch download daily data once
        daily_data = yf.download(tickers=tickers, period="1y", interval="1d", group_by='ticker', threads=True, progress=False)
        
        swing_list, intraday_list, btst_list = [], [], []

        for ticker in tickers:
            try:
                if isinstance(daily_data.columns, pd.MultiIndex):
                    if ticker not in daily_data.columns.levels[0]:
                        continue
                    df = daily_data[ticker].dropna()
                else:
                    df = daily_data.dropna()

                if len(df) < 50:
                    continue

                # Vectorized Technical Calculations
                close = df['Close']
                high = df['High']
                low = df['Low']
                volume = df['Volume']

                ema20 = ta.trend.ema_indicator(close, window=20)
                ema50 = ta.trend.ema_indicator(close, window=50)
                ema200 = ta.trend.ema_indicator(close, window=200)
                rsi = ta.momentum.rsi(close, window=14)
                atr = ta.volatility.average_true_range(high, low, close, window=14)
                vol_sma20 = volume.rolling(20).mean()
                high_20 = high.rolling(20).max()

                curr_close = close.iloc[-1]
                prev_close = close.iloc[-2]
                curr_high = high.iloc[-1]
                curr_low = low.iloc[-1]
                curr_vol = volume.iloc[-1]
                curr_vol_sma = vol_sma20.iloc[-1]
                curr_rsi = rsi.iloc[-1]
                curr_atr = atr.iloc[-1]
                curr_ema20 = ema20.iloc[-1]
                curr_ema50 = ema50.iloc[-1]
                curr_ema200 = ema200.iloc[-1]
                prev_high20 = high_20.iloc[-2]

                # Minimum liquidity filter: ₹50 Lakhs turnover
                if (curr_close * curr_vol_sma) < 5_000_000:
                    continue

                clean_symbol = ticker.replace(".NS", "")
                vol_mult = round(curr_vol / curr_vol_sma, 2) if curr_vol_sma > 0 else 1.0

                # 1. SWING SETUP: Strong trend, 20-day high breakout, surge volume
                if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (55 <= curr_rsi <= 72) and (curr_close >= prev_high20 * 0.995) and (vol_mult >= 1.3):
                    sl = curr_close - (2 * curr_atr)
                    tgt = curr_close + (4 * curr_atr)
                    qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                    swing_list.append({
                        "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                        "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                    })

                # 2. INTRADAY SETUP: Momentum continuation, volume spike, short-term EMA support
                if (curr_close > curr_ema20) and (curr_rsi >= 58) and (vol_mult >= 1.5):
                    sl = curr_close - (1.2 * curr_atr)
                    tgt = curr_close + (2.5 * curr_atr)
                    qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                    intraday_list.append({
                        "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                        "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                    })

                # 3. BTST SETUP: Closing near high of day, high volume surge, strong RSI momentum
                day_range = curr_high - curr_low
                close_location = (curr_close - curr_low) / day_range if day_range > 0 else 0
                if (close_location >= 0.82) and (curr_rsi >= 60) and (vol_mult >= 1.8) and (curr_close > prev_close):
                    sl = curr_close - (1.5 * curr_atr)
                    tgt = curr_close + (2.0 * curr_atr)
                    qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                    btst_list.append({
                        "Ticker": clean_symbol, "Price": round(curr_close, 2), "Close_Near_High_%": round(close_location * 100, 1),
                        "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                    })

            except Exception:
                continue

        return {
            "SWING": pd.DataFrame(swing_list),
            "INTRADAY": pd.DataFrame(intraday_list),
            "BTST": pd.DataFrame(btst_list)
        }

    def analyze_stock(self, symbol):
        """Runs single-stock diagnostic check."""
        ticker_symbol = f"{symbol.upper()}.NS" if not symbol.endswith(".NS") else symbol.upper()
        df = yf.download(ticker_symbol, period="1y", interval="1d", progress=False)
        
        if df.empty:
            return {"Error": f"No market data found for symbol: {symbol}"}
            
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        ema20 = ta.trend.ema_indicator(close, window=20).iloc[-1]
        ema50 = ta.trend.ema_indicator(close, window=50).iloc[-1]
        ema200 = ta.trend.ema_indicator(close, window=200).iloc[-1]
        rsi = ta.momentum.rsi(close, window=14).iloc[-1]
        atr = ta.volatility.average_true_range(high, low, close, window=14).iloc[-1]
        vol_sma = volume.rolling(20).mean().iloc[-1]
        curr_price = close.iloc[-1]
        curr_vol = volume.iloc[-1]
        
        score = 0
        reasons = []
        
        if curr_price > ema200:
            score += 25
            reasons.append("✓ Above 200-day EMA (Macro Bullish)")
        else:
            reasons.append("✗ Below 200-day EMA (Macro Bearish)")
            
        if ema20 > ema50:
            score += 25
            reasons.append("✓ 20 EMA > 50 EMA (Short-term Uptrend)")
        else:
            reasons.append("✗ 20 EMA < 50 EMA (Short-term Downtrend)")
            
        if 50 <= rsi <= 70:
            score += 25
            reasons.append(f"✓ RSI at {round(rsi, 1)} (Strong Momentum)")
        else:
            reasons.append(f"✗ RSI at {round(rsi, 1)} (Weak/Overbought Momentum)")
            
        if curr_vol > vol_sma:
            score += 25
            reasons.append("✓ Volume is above 20-day average")
        else:
            reasons.append("✗ Volume is below 20-day average")
            
        return {
            "Symbol": ticker_symbol.replace(".NS", ""),
            "Score": score,
            "Price": round(curr_price, 2),
            "RSI": round(rsi, 1),
            "ATR": round(atr, 2),
            "EMA200": round(ema200, 2),
            "StopLoss": round(curr_price - (2 * atr), 2),
            "Target": round(curr_price + (4 * atr), 2),
            "Reasons": reasons
        }

    def run(self):
        """Default entry runner."""
        return self.scan_all_strategies()
