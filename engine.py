import io
import warnings
import requests
import pandas as pd
import yfinance as yf
import ta

# Silence NumPy/Pandas deprecation warnings thrown by yfinance internals
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

class PaperEngine:
    """
    Core engine handling NSE stock data fetching, technical indicator calculations,
    and trading strategy scanning.
    """
    def __init__(self, initial_balance=100000.0, risk_per_trade_pct=1.0):
        self.balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.positions = {}
        self.swing_results = pd.DataFrame()
        self.intraday_results = pd.DataFrame()

    def fetch_nse_universe(self, index_name="NIFTY 500"):
        """Downloads active stock list from NSE archives appended with '.NS'."""
        urls = {
            "NIFTY 50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
            "NIFTY NEXT 50": "https://archives.nseindia.com/content/indices/ind_niftynext50list.csv",
            "NIFTY 500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        }
        url = urls.get(index_name, urls["NIFTY 500"])
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            df = pd.read_csv(io.StringIO(res.text))
            return [f"{symbol}.NS" for symbol in df['Symbol'].tolist()]
        except Exception as e:
            print(f"[!] Warning: Could not fetch official NSE list ({e}). Falling back to default list.")
            return [
                "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
                "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "AXISBANK.NS"
            ]

    def calculate_indicators(self, df):
        """Calculates indicators used across Intraday and Swing strategies."""
        df = df.copy()
        
        # Exponential Moving Averages
        df['EMA_20'] = ta.trend.ema_indicator(df['Close'], window=20)
        df['EMA_50'] = ta.trend.ema_indicator(df['Close'], window=50)
        df['EMA_200'] = ta.trend.ema_indicator(df['Close'], window=200)
        
        # Momentum & Volatility
        df['RSI'] = ta.momentum.rsi(df['Close'], window=14)
        df['ATR'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], window=14)
        
        # Volume Indicators
        df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
        df['Vol_Surge'] = df['Volume'] > (df['Vol_SMA20'] * 1.5)
        
        # VWAP Approximation
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
        
        # 20-Period High Benchmark
        df['High_20'] = df['High'].rolling(20).max()
        
        return df

    def scan_markets(self, tickers=None, mode="SWING"):
        """Scans specified stock universe for intraday or swing setups."""
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 500")
            
        interval = "15m" if mode == "INTRADAY" else "1d"
        period = "5d" if mode == "INTRADAY" else "1y"
        risk_amount = self.balance * (self.risk_per_trade_pct / 100.0)
        
        # Key Fix: progress=False prevents yfinance terminal spam, threads=True speeds up bulk fetch
        data = yf.download(
            tickers=tickers, 
            period=period, 
            interval=interval, 
            group_by='ticker', 
            threads=True, 
            progress=False
        )
        
        results = []
        
        for ticker in tickers:
            try:
                # Handle single vs multi-index data structure returned by yfinance
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.levels[0]:
                        continue
                    df = data[ticker].dropna()
                else:
                    df = data.dropna()
                    
                if len(df) < 50:
                    continue
                    
                df = self.calculate_indicators(df)
                curr = df.iloc[-1]
                prev = df.iloc[-2]
                
                # Liquidity filter: Minimum ₹50 Lakhs turnover
                if (curr['Close'] * curr['Vol_SMA20']) < 5_000_000:
                    continue
                    
                if mode == "SWING":
                    cond_trend = (curr['EMA_20'] > curr['EMA_50']) and (curr['Close'] > curr['EMA_200'])
                    cond_rsi = 55 <= curr['RSI'] <= 70
                    cond_vol = curr['Vol_Surge']
                    cond_breakout = curr['Close'] >= (prev['High_20'] * 0.995)
                    
                    if cond_trend and cond_rsi and cond_vol and cond_breakout:
                        entry = curr['Close']
                        sl = entry - (2 * curr['ATR'])
                        target = entry + (4 * curr['ATR'])
                        risk_per_share = entry - sl
                        qty = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                        
                        results.append({
                            "Ticker": ticker.replace(".NS", ""),
                            "Price": round(entry, 2),
                            "RSI": round(curr['RSI'], 1),
                            "Vol_Mult": round(curr['Volume'] / curr['Vol_SMA20'], 2),
                            "StopLoss": round(sl, 2),
                            "Target": round(target, 2),
                            "Qty": qty,
                            "Est_Position": round(qty * entry, 2)
                        })
                        
                elif mode == "INTRADAY":
                    cond_vwap = curr['Close'] > curr['VWAP']
                    cond_ema = curr['Close'] > curr['EMA_20']
                    cond_rsi = curr['RSI'] > 58
                    cond_vol = curr['Volume'] > (curr['Vol_SMA20'] * 1.8)
                    
                    if cond_vwap and cond_ema and cond_rsi and cond_vol:
                        entry = curr['Close']
                        sl = entry - (1.5 * curr['ATR'])
                        target = entry + (3.0 * curr['ATR'])
                        risk_per_share = entry - sl
                        qty = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                        
                        results.append({
                            "Ticker": ticker.replace(".NS", ""),
                            "Price": round(entry, 2),
                            "RSI": round(curr['RSI'], 1),
                            "VWAP": round(curr['VWAP'], 2),
                            "StopLoss": round(sl, 2),
                            "Target": round(target, 2),
                            "Qty": qty,
                            "Est_Position": round(qty * entry, 2)
                        })
            except Exception:
                continue
                
        return pd.DataFrame(results)

    def run(self):
        """Main execution interface."""
        universe = self.fetch_nse_universe("NIFTY 500")
        self.swing_results = self.scan_markets(universe, mode="SWING")
        self.intraday_results = self.scan_markets(universe, mode="INTRADAY")
        
        return {
            "status": "Success",
            "swing_candidates": self.swing_results,
            "intraday_candidates": self.intraday_results
        }
