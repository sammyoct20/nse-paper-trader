import sys
import subprocess

# ==========================================
# 0. DEPENDENCY CHECK & AUTO-INSTALLATION
# ==========================================
REQUIRED_PACKAGES = ["pandas", "yfinance", "requests", "ta"]

def install_and_import(package):
    """Ensures required packages are installed before importing."""
    try:
        __import__(package)
    except ImportError:
        print(f"[!] Package '{package}' not found. Auto-installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

for pkg in REQUIRED_PACKAGES:
    install_and_import(pkg)

# --- Standard Library Imports ---
import io
import requests
import pandas as pd
import yfinance as yf
import ta

# ==========================================
# 1. DYNAMIC NSE TICKER FETCHING
# ==========================================
def fetch_nse_universe(index_name="NIFTY 500"):
    """
    Downloads official Nifty stock lists directly from NSE archives
    and formats them for yfinance with '.NS'.
    """
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
        tickers = [f"{symbol}.NS" for symbol in df['Symbol'].tolist()]
        print(f"[+] Successfully loaded {len(tickers)} active tickers for {index_name}.")
        return tickers
    except Exception as e:
        print(f"[!] Warning: Could not fetch official NSE list ({e}). Using core fallback liquid tickers.")
        return [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
            "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "AXISBANK.NS"
        ]

# ==========================================
# 2. TECHNICAL INDICATOR ENGINE
# ==========================================
def calculate_indicators(df):
    """Calculates high-confluence technical indicators."""
    df = df.copy()
    
    # Exponential Moving Averages
    df['EMA_20'] = ta.trend.ema_indicator(df['Close'], window=20)
    df['EMA_50'] = ta.trend.ema_indicator(df['Close'], window=50)
    df['EMA_200'] = ta.trend.ema_indicator(df['Close'], window=200)
    
    # Relative Strength Index (RSI)
    df['RSI'] = ta.momentum.rsi(df['Close'], window=14)
    
    # Average True Range (ATR) for Volatility Sizing
    df['ATR'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], window=14)
    
    # Volume Profiles
    df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
    df['Vol_Surge'] = df['Volume'] > (df['Vol_SMA20'] * 1.5)
    
    # Volume-Weighted Average Price (VWAP)
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    
    # Breakout Benchmarks
    df['High_20'] = df['High'].rolling(20).max()
    
    return df

# ==========================================
# 3. CONFLUENCE MARKET SCANNER
# ==========================================
def scan_markets(tickers, mode="SWING", total_capital=100000, risk_per_trade_pct=1.0):
    """
    Scans the NSE universe for high-win-rate setups with automated position sizing.
    
    Parameters:
      - mode: 'SWING' (1d candles) or 'INTRADAY' (15m candles)
      - total_capital: Your total trading equity in INR (₹)
      - risk_per_trade_pct: Maximum account equity percentage risked per trade
    """
    interval = "15m" if mode == "INTRADAY" else "1d"
    period = "5d" if mode == "INTRADAY" else "1y"
    risk_amount = total_capital * (risk_per_trade_pct / 100.0)
    
    print(f"\n[*] Scanning {mode} setups across {len(tickers)} stocks...")
    data = yf.download(tickers=tickers, period=period, interval=interval, group_by='ticker', threads=True)
    
    results = []
    
    for ticker in tickers:
        try:
            df = data[ticker].dropna()
            if len(df) < 50:
                continue
                
            df = calculate_indicators(df)
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Liquidity Filter: Daily turnover check (Min ₹50 Lakhs)
            if (curr['Close'] * curr['Vol_SMA20']) < 5_000_000:
                continue
                
            if mode == "SWING":
                # High-Win Rate Criteria:
                # 1. Trend: EMA 20 > 50 & Price > 200 EMA
                # 2. RSI: Bullish momentum zone (55 to 70)
                # 3. Volume: Volume > 1.5x 20-period average
                # 4. Price near 20-day High
                cond_trend = (curr['EMA_20'] > curr['EMA_50']) and (curr['Close'] > curr['EMA_200'])
                cond_rsi = 55 <= curr['RSI'] <= 70
                cond_vol = curr['Vol_Surge']
                cond_breakout = curr['Close'] >= (prev['High_20'] * 0.995)
                
                if cond_trend and cond_rsi and cond_vol and cond_breakout:
                    entry = curr['Close']
                    sl = entry - (2 * curr['ATR'])
                    target = entry + (4 * curr['ATR'])  # 1:2 Risk-Reward Ratio
                    risk_per_share = entry - sl
                    
                    # Calculated position sizing based on risk limit
                    qty = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                    
                    results.append({
                        "Ticker": ticker.replace(".NS", ""),
                        "Price (₹)": round(entry, 2),
                        "RSI": round(curr['RSI'], 1),
                        "Vol_Mult": round(curr['Volume'] / curr['Vol_SMA20'], 2),
                        "StopLoss (₹)": round(sl, 2),
                        "Target (₹)": round(target, 2),
                        "Qty (Shares)": qty,
                        "Est. Position (₹)": round(qty * entry, 2)
                    })
                    
            elif mode == "INTRADAY":
                # High-Win Rate Intraday Criteria:
                # 1. Price above VWAP and 20 EMA
                # 2. RSI > 58
                # 3. Volume > 1.8x average
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
                        "Price (₹)": round(entry, 2),
                        "RSI": round(curr['RSI'], 1),
                        "VWAP (₹)": round(curr['VWAP'], 2),
                        "StopLoss (₹)": round(sl, 2),
                        "Target (₹)": round(target, 2),
                        "Qty (Shares)": qty,
                        "Est. Position (₹)": round(qty * entry, 2)
                    })
                    
        except Exception:
            continue
            
    return pd.DataFrame(results)

# ==========================================
# 4. SINGLE STOCK ANALYZER
# ==========================================
def analyze_stock(symbol):
    """Executes a diagnostic technical evaluation for a single NSE stock ticker."""
    ticker_symbol = f"{symbol.upper()}.NS" if not symbol.endswith(".NS") else symbol.upper()
    print(f"\n==========================================")
    print(f"      SINGLE STOCK DIAGNOSTIC: {ticker_symbol}     ")
    print(f"==========================================")
    
    df = yf.download(ticker_symbol, period="1y", interval="1d")
    if df.empty:
        print("[!] Symbol data not found.")
        return
        
    df = calculate_indicators(df)
    curr = df.iloc[-1]
    
    score = 0
    reasons = []
    
    # 200 EMA Macro Trend Test
    if curr['Close'] > curr['EMA_200']:
        score += 25
        reasons.append("✓ Price above 200-day EMA (Long-term Bullish)")
    else:
        reasons.append("✗ Price below 200-day EMA (Long-term Bearish)")
        
    # Moving Average Alignment Test
    if curr['EMA_20'] > curr['EMA_50']:
        score += 25
        reasons.append("✓ 20 EMA > 50 EMA (Short-term Uptrend Alignment)")
    else:
        reasons.append("✗ 20 EMA < 50 EMA (Short-term Downtrend Alignment)")
        
    # RSI Momentum Test
    if 50 <= curr['RSI'] <= 70:
        score += 25
        reasons.append(f"✓ RSI at {round(curr['RSI'], 1)} (Strong Momentum Zone)")
    elif curr['RSI'] > 70:
        score += 10
        reasons.append(f"⚠️ RSI at {round(curr['RSI'], 1)} (Overbought Zone)")
    else:
        reasons.append(f"✗ RSI at {round(curr['RSI'], 1)} (Weak Momentum)")
        
    # Institutional Volume Test
    if curr['Volume'] > curr['Vol_SMA20']:
        score += 25
        reasons.append("✓ Volume exceeds 20-day Average")
    else:
        reasons.append("✗ Volume below 20-day Average")
        
    print(f"Overall Technical Score: {score}/100")
    print(f"Current Price:           ₹{round(curr['Close'], 2)}")
    print(f"200-day EMA:             ₹{round(curr['EMA_200'], 2)}")
    print(f"14-day ATR:              ₹{round(curr['ATR'], 2)}")
    print("\nDetailed Diagnostic Breakdown:")
    for r in reasons:
        print(f"  {r}")
        
    print("\nTrade Execution Levels:")
    print(f"  Stop Loss (2x ATR):                  ₹{round(curr['Close'] - (2 * curr['ATR']), 2)}")
    print(f"  Target Level (1:2 Risk/Reward):     ₹{round(curr['Close'] + (4 * curr['ATR']), 2)}")

# ==========================================
# 5. MAIN EXECUTION ENTRY POINT
# ==========================================
if __name__ == "__main__":
    # Assumptions for default risk calculation
    TOTAL_CAPITAL = 100000       # ₹1,00,000 Portfolio
    RISK_PER_TRADE_PCT = 1.0     # Risk 1.0% (₹1,000) per trade
    
    # Fetch universe
    universe = fetch_nse_universe("NIFTY 500")
    
    # Execute Swing Scan
    swing_results = scan_markets(
        universe, 
        mode="SWING", 
        total_capital=TOTAL_CAPITAL, 
        risk_per_trade_pct=RISK_PER_TRADE_PCT
    )
    
    print("\n------------------------------------------------------------")
    print("                 TOP SWING TRADE CANDIDATES                 ")
    print("------------------------------------------------------------")
    if not swing_results.empty:
        print(swing_results.to_string(index=False))
    else:
        print("No swing trade candidates met all technical criteria today.")
        
    # Execute Single Stock Analysis Example
    analyze_stock("RELIANCE")
