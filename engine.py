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
    High-performance trading scanner with strict institutional filters:
    - Intraday: Volume > 1.5x + RSI >= 56 + Price > 20 EMA
    - BTST: Volume > 1.5x + RSI (58-75) + Candle Close Loc >= 80% + Price > Prev Close
    - Swing: Volume > 1.2x + RSI (53-72) + 20-Day High Breakout + (20 EMA > 50 EMA & Price > 200 EMA)
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
            return [
                "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
                "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "AXISBANK.NS"
            ]

    def _flatten_df(self, df):
        """Converts yfinance 2D DataFrame into 1D Series safely."""
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    def scan_all_strategies(self, tickers=None, top_n=5):
        """
        Scans stock universe with strict parameter filters and returns top N results per strategy.
        """
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 500")
            
        risk_amount = self.balance * (self.risk_per_trade_pct / 100.0)
        swing_list, intraday_list, btst_list = [], [], []

        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            
            try:
                data = yf.download(
                    tickers=chunk, 
                    period="1y", 
                    interval="1d", 
                    group_by='ticker', 
                    threads=True, 
                    progress=False
                )
            except Exception:
                continue

            for ticker in chunk:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if ticker not in data.columns.levels[0]:
                            continue
                        df = data[ticker].dropna()
                    else:
                        df = data.dropna()

                    if len(df) < 50:
                        continue

                    close = df['Close'].squeeze()
                    high = df['High'].squeeze()
                    low = df['Low'].squeeze()
                    volume = df['Volume'].squeeze()

                    # Technical Indicators
                    ema20 = ta.trend.ema_indicator(close, window=20)
                    ema50 = ta.trend.ema_indicator(close, window=50)
                    ema200 = ta.trend.ema_indicator(close, window=200)
                    rsi = ta.momentum.rsi(close, window=14)
                    atr = ta.volatility.average_true_range(high, low, close, window=14)
                    vol_sma20 = volume.rolling(20).mean()
                    high_20 = high.rolling(20).max()

                    curr_close = float(close.iloc[-1])
                    prev_close = float(close.iloc[-2])
                    curr_high = float(high.iloc[-1])
                    curr_low = float(low.iloc[-1])
                    curr_vol = float(volume.iloc[-1])
                    curr_vol_sma = float(vol_sma20.iloc[-1])
                    curr_rsi = float(rsi.iloc[-1])
                    curr_atr = float(atr.iloc[-1])
                    curr_ema20 = float(ema20.iloc[-1])
                    curr_ema50 = float(ema50.iloc[-1])
                    curr_ema200 = float(ema200.iloc[-1])
                    prev_high20 = float(high_20.iloc[-2])

                    # Minimum Turnover Liquidity Filter: ₹50 Lakhs
                    if (curr_close * curr_vol_sma) < 5_000_000:
                        continue

                    clean_symbol = ticker.replace(".NS", "")
                    vol_mult = round(curr_vol / curr_vol_sma, 2) if curr_vol_sma > 0 else 1.0

                    # 1. STRICT SWING FILTER
                    cond_swing_trend = (curr_ema20 > curr_ema50) and (curr_close > curr_ema200)
                    cond_swing_rsi = 53 <= curr_rsi <= 72
                    cond_swing_vol = vol_mult >= 1.2
                    cond_swing_breakout = curr_close >= (prev_high20 * 0.99)

                    if cond_swing_trend and cond_swing_rsi and cond_swing_vol and cond_swing_breakout:
                        sl = curr_close - (2.0 * curr_atr)
                        tgt = curr_close + (4.0 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        swing_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                            "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                        })

                    # 2. STRICT INTRADAY FILTER
                    cond_intra_ema = curr_close > curr_ema20
                    cond_intra_rsi = curr_rsi >= 56
                    cond_intra_vol = vol_mult >= 1.5

                    if cond_intra_ema and cond_intra_rsi and cond_intra_vol:
                        sl = curr_close - (1.2 * curr_atr)
                        tgt = curr_close + (2.5 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        intraday_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                            "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                        })

                    # 3. STRICT BTST FILTER
                    day_range = curr_high - curr_low
                    close_location = (curr_close - curr_low) / day_range if day_range > 0 else 0
                    cond_btst_close = close_location >= 0.80
                    cond_btst_rsi = 58 <= curr_rsi <= 75
                    cond_btst_vol = vol_mult >= 1.5
                    cond_btst_green = curr_close > prev_close

                    if cond_btst_close and cond_btst_rsi and cond_btst_vol and cond_btst_green:
                        sl = curr_close - (1.5 * curr_atr)
                        tgt = curr_close + (2.0 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        btst_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "Close_High_%": round(close_location * 100, 1),
                            "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                        })

                except Exception:
                    continue

        # Sort results by highest Volume Multiplier and pick top N
        df_swing = pd.DataFrame(swing_list)
        if not df_swing.empty:
            df_swing = df_swing.sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True)

        df_intraday = pd.DataFrame(intraday_list)
        if not df_intraday.empty:
            df_intraday = df_intraday.sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True)

        df_btst = pd.DataFrame(btst_list)
        if not df_btst.empty:
            df_btst = df_btst.sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True)

        return {
            "SWING": df_swing,
            "INTRADAY": df_intraday,
            "BTST": df_btst
        }

    def analyze_stock(self, symbol):
        """Generates single stock diagnostic."""
        ticker_symbol = f"{symbol.upper()}.NS" if not symbol.endswith(".NS") else symbol.upper()
        df = yf.download(ticker_symbol, period="1y", interval="1d", progress=False)
        
        if df.empty:
            return {"Error": f"No data found for symbol: {symbol}"}
            
        df = self._flatten_df(df)
        
        close = df['Close'].squeeze()
        high = df['High'].squeeze()
        low = df['Low'].squeeze()
        volume = df['Volume'].squeeze()
        
        ema20 = ta.trend.ema_indicator(close, window=20).iloc[-1]
        ema50 = ta.trend.ema_indicator(close, window=50).iloc[-1]
        ema200 = ta.trend.ema_indicator(close, window=200).iloc[-1]
        rsi = ta.momentum.rsi(close, window=14).iloc[-1]
        atr = ta.volatility.average_true_range(high, low, close, window=14).iloc[-1]
        vol_sma = volume.rolling(20).mean().iloc[-1]
        curr_price = float(close.iloc[-1])
        curr_vol = float(volume.iloc[-1])
        
        score = 0
        reasons = []
        
        if curr_price > ema200:
            score += 25
            reasons.append("✓ Price above 200-day EMA (Macro Bullish)")
        else:
            reasons.append("✗ Price below 200-day EMA (Macro Bearish)")
            
        if ema20 > ema50:
            score += 25
            reasons.append("✓ 20 EMA > 50 EMA (Short-term Uptrend)")
        else:
            reasons.append("✗ 20 EMA < 50 EMA (Short-term Downtrend)")
            
        if 50 <= rsi <= 72:
            score += 25
            reasons.append(f"✓ RSI at {round(float(rsi), 1)} (Strong Momentum)")
        else:
            reasons.append(f"✗ RSI at {round(float(rsi), 1)} (Weak/Extreme Momentum)")
            
        if curr_vol > vol_sma:
            score += 25
            reasons.append("✓ Daily volume above 20-day average")
        else:
            reasons.append("✗ Daily volume below 20-day average")
            
        return {
            "Symbol": ticker_symbol.replace(".NS", ""),
            "Score": score,
            "Price": round(curr_price, 2),
            "RSI": round(float(rsi), 1),
            "ATR": round(float(atr), 2),
            "EMA200": round(float(ema200), 2),
            "StopLoss": round(curr_price - (2 * float(atr)), 2),
            "Target": round(curr_price + (4 * float(atr)), 2),
            "Reasons": reasons
        }
