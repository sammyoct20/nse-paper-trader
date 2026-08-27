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
    BTST setups, and historical backtesting for NSE equities.
    """
    def __init__(self, initial_balance=100000.0, risk_per_trade_pct=1.0):
        self.balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct

    def fetch_nse_universe(self, index_name="NIFTY 50"):
        """Fetches ticker lists dynamically from NSE archives."""
        urls = {
            "NIFTY 50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
            "NIFTY NEXT 50": "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
            "NIFTY 500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        }
        url = urls.get(index_name, urls["NIFTY 50"])
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
        """Fixes yfinance MultiIndex issue by converting columns into 1D Series safely."""
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    def scan_all_strategies(self, tickers=None):
        """Chunked market scanner evaluating Swing, Intraday, and BTST setups."""
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 50")
            
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

                    if (curr_close * curr_vol_sma) < 5_000_000:
                        continue

                    clean_symbol = ticker.replace(".NS", "")
                    vol_mult = round(curr_vol / curr_vol_sma, 2) if curr_vol_sma > 0 else 1.0

                    # 1. SWING SETUP
                    if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (curr_close >= prev_high20 * 0.99) and (vol_mult >= 1.2):
                        sl = curr_close - (2 * curr_atr)
                        tgt = curr_close + (4 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        swing_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                            "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                        })

                    # 2. INTRADAY SETUP
                    if (curr_close > curr_ema20) and (curr_rsi >= 56) and (vol_mult >= 1.4):
                        sl = curr_close - (1.2 * curr_atr)
                        tgt = curr_close + (2.5 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        intraday_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                            "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty
                        })

                    # 3. BTST SETUP
                    day_range = curr_high - curr_low
                    close_location = (curr_close - curr_low) / day_range if day_range > 0 else 0
                    if (close_location >= 0.80) and (curr_rsi >= 58) and (vol_mult >= 1.5) and (curr_close > prev_close):
                        sl = curr_close - (1.5 * curr_atr)
                        tgt = curr_close + (2.0 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        btst_list.append({
                            "Ticker": clean_symbol, "Price": round(curr_close, 2), "Close_High_%": round(close_location * 100, 1),
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
        """Single-stock diagnostic."""
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
            
        if 50 <= rsi <= 70:
            score += 25
            reasons.append(f"✓ RSI at {round(float(rsi), 1)} (Strong Momentum)")
        else:
            reasons.append(f"✗ RSI at {round(float(rsi), 1)} (Weak/Extreme Momentum)")
            
        if curr_vol > vol_sma:
            score += 25
            reasons.append("✓ Volume is above 20-day average")
        else:
            reasons.append("✗ Volume is below 20-day average")
            
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

    def backtest_strategy(self, ticker="RELIANCE.NS", strategy_type="SWING", days=365):
        """Historical strategy backtester."""
        ticker_symbol = f"{ticker.upper()}.NS" if not ticker.endswith(".NS") else ticker.upper()
        df = yf.download(ticker_symbol, period=f"{days}d", interval="1d", progress=False)
        if df.empty:
            return {"Error": f"No historical data for {ticker_symbol}"}
            
        df = self._flatten_df(df)
        close = df['Close'].squeeze()
        high = df['High'].squeeze()
        low = df['Low'].squeeze()
        volume = df['Volume'].squeeze()
        
        df['EMA20'] = ta.trend.ema_indicator(close, window=20)
        df['EMA50'] = ta.trend.ema_indicator(close, window=50)
        df['EMA200'] = ta.trend.ema_indicator(close, window=200)
        df['RSI'] = ta.momentum.rsi(close, window=14)
        df['ATR'] = ta.volatility.average_true_range(high, low, close, window=14)
        df['Vol_SMA20'] = volume.rolling(20).mean()
        df['High_20'] = high.rolling(20).max()
        
        trades = []
        in_trade = False
        entry_price, sl, tgt, entry_date = 0, 0, 0, None
        
        for i in range(50, len(df)):
            curr_close = float(df['Close'].iloc[i])
            curr_high = float(df['High'].iloc[i])
            curr_low = float(df['Low'].iloc[i])
            curr_rsi = float(df['RSI'].iloc[i])
            curr_atr = float(df['ATR'].iloc[i])
            curr_ema20 = float(df['EMA20'].iloc[i])
            curr_ema50 = float(df['EMA50'].iloc[i])
            curr_ema200 = float(df['EMA200'].iloc[i])
            vol_mult = float(df['Volume'].iloc[i] / df['Vol_SMA20'].iloc[i]) if df['Vol_SMA20'].iloc[i] > 0 else 1.0
            prev_high20 = float(df['High_20'].iloc[i-1])
            date = df.index[i].strftime("%Y-%m-%d")
            
            if in_trade:
                if curr_low <= sl:
                    trades.append({
                        "Entry Date": entry_date, "Exit Date": date, "Type": strategy_type,
                        "Entry": entry_price, "Exit": sl, "Status": "STOP LOSS",
                        "P&L %": round(((sl - entry_price) / entry_price) * 100, 2)
                    })
                    in_trade = False
                elif curr_high >= tgt:
                    trades.append({
                        "Entry Date": entry_date, "Exit Date": date, "Type": strategy_type,
                        "Entry": entry_price, "Exit": tgt, "Status": "TARGET HIT",
                        "P&L %": round(((tgt - entry_price) / entry_price) * 100, 2)
                    })
                    in_trade = False
            elif not in_trade:
                signal = False
                if strategy_type == "SWING":
                    signal = (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (curr_close >= prev_high20 * 0.99) and (vol_mult >= 1.2)
                    mult_sl, mult_tgt = 2.0, 4.0
                elif strategy_type == "BTST":
                    day_range = curr_high - curr_low
                    close_loc = (curr_close - curr_low) / day_range if day_range > 0 else 0
                    signal = (close_loc >= 0.80) and (curr_rsi >= 58) and (vol_mult >= 1.5)
                    mult_sl, mult_tgt = 1.5, 2.0
                    
                if signal:
                    in_trade = True
                    entry_price = curr_close
                    sl = entry_price - (mult_sl * curr_atr)
                    tgt = entry_price + (mult_tgt * curr_atr)
                    entry_date = date

        trades_df = pd.DataFrame(trades)
        if trades_df.empty:
            return {"Error": "No signals triggered during this backtest window."}
            
        wins = trades_df[trades_df['Status'] == 'TARGET HIT']
        win_rate = round((len(wins) / len(trades_df)) * 100, 1)
        total_return = round(trades_df['P&L %'].sum(), 2)
        
        return {
            "Symbol": ticker_symbol.replace(".NS", ""),
            "Total Trades": len(trades_df),
            "Win Rate %": win_rate,
            "Total Return %": total_return,
            "Trades Ledger": trades_df
        }

    def run(self):
        return self.scan_all_strategies()
