import io
import os
import time
import logging
import warnings
from datetime import datetime, date, timedelta
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import ta

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("unified_engine")

# -------------------------------------------------------------------
# OPTIONS CLIENT & HELPERS
# -------------------------------------------------------------------
VALID_INDEX_SYMBOLS = {"NIFTY", "SENSEX"}
INDEX_YF_TICKERS = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}

class NSEOptionsClient:
    BASE = "https://www.nseindia.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/option-chain",
        })
        self._warm_up()

    def _warm_up(self):
        try:
            self.session.get(self.BASE, timeout=10)
            self.session.get(f"{self.BASE}/option-chain", timeout=10)
        except Exception as e:
            log.warning(f"NSE session warm-up failed: {e}")

    def fetch_chain(self, index_symbol: str) -> dict:
        if index_symbol != "NIFTY":
            raise ValueError(f"NSE client only supports NIFTY directly: {index_symbol}")
        url = f"{self.BASE}/api/option-chain-indices"
        r = self.session.get(url, params={"symbol": index_symbol}, timeout=15)
        r.raise_for_status()
        return r.json()

def nearest_expiry_date(target_weekday: int) -> str:
    """Calculates upcoming target weekday date string (0=Mon, 1=Tue, 3=Thu)."""
    today = datetime.now().date()
    days_ahead = target_weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    next_date = today + timedelta(days=days_ahead)
    return next_date.strftime("%d-%b-%Y").upper()

def atm_strike(spot_price: float, step: float = 50.0) -> float:
    return float(round(spot_price / step) * step)

def get_contract(chain_json: dict, strike: float, expiry: str, option_type: str) -> dict | None:
    for row in chain_json.get("records", {}).get("data", []):
        if row.get("strikePrice") == strike and row.get("expiryDate") == expiry:
            leg = row.get(option_type)
            if leg and leg.get("lastPrice") is not None:
                return {
                    "ltp": float(leg["lastPrice"]),
                    "oi": leg.get("openInterest"),
                    "iv": leg.get("impliedVolatility"),
                }
    return None

def spot_price(chain_json: dict) -> float | None:
    val = chain_json.get("records", {}).get("underlyingValue")
    return float(val) if val is not None else None

# -------------------------------------------------------------------
# MAIN CORE ENGINE: PAPER ENGINE (STOCKS & OPTIONS)
# -------------------------------------------------------------------
class PaperEngine:
    def __init__(self, initial_balance=100000.0, risk_per_trade_pct=1.0):
        self.balance = float(os.getenv("OPTIONS_CAPITAL", initial_balance))
        self.risk_per_trade_pct = risk_per_trade_pct
        
        # Options Config (NIFTY: 65, SENSEX: 20)
        self.options_indices = ["NIFTY", "SENSEX"]
        self.lot_sizes = {
            "NIFTY": int(os.getenv("NIFTY_LOT_SIZE", 65)),
            "SENSEX": int(os.getenv("SENSEX_LOT_SIZE", 20))
        }
        self.opt_stop_loss_pct = float(os.getenv("OPTIONS_STOP_LOSS_PCT", 0.30))
        self.opt_target_pct = float(os.getenv("OPTIONS_TARGET_PCT", 0.50))
        
        self.nse_opt = NSEOptionsClient()
        self.db_dsn = os.getenv("DATABASE_URL")
        self.ensure_schema()

    def _get_connection(self):
        if self.db_dsn:
            import psycopg2
            return psycopg2.connect(self.db_dsn)
        else:
            import sqlite3
            return sqlite3.connect("trading_paper.db")

    def ensure_schema(self):
        conn = self._get_connection()
        cur = conn.cursor()
        if self.db_dsn:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS options_trades (
                id SERIAL PRIMARY KEY, contract_name TEXT, index_symbol TEXT, option_type TEXT,
                strike FLOAT, expiry TEXT, lot_size INT, lots INT,
                entry_premium FLOAT, stop_loss_premium FLOAT, target_premium FLOAT,
                status TEXT DEFAULT 'OPEN', entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                exit_time TIMESTAMP, exit_premium FLOAT, exit_reason TEXT, pnl FLOAT
            );
            """)
        else:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS options_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, contract_name TEXT, index_symbol TEXT, option_type TEXT,
                strike REAL, expiry TEXT, lot_size INTEGER, lots INTEGER,
                entry_premium REAL, stop_loss_premium REAL, target_premium REAL,
                status TEXT DEFAULT 'OPEN', entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                exit_time TIMESTAMP, exit_premium REAL, exit_reason TEXT, pnl REAL
            );
            """)
        conn.commit()
        conn.close()

    def fetch_nse_universe(self, index_name="NIFTY 500"):
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
            return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "SBIN.NS"]

    def scan_all_strategies(self, tickers=None, top_n=5):
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 500")
            
        risk_amount = self.balance * (self.risk_per_trade_pct / 100.0)
        swing_list, intraday_list, btst_list = [], [], []

        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            try:
                data = yf.download(tickers=chunk, period="1y", interval="1d", group_by='ticker', threads=True, progress=False)
            except Exception:
                continue

            for ticker in chunk:
                try:
                    df = data[ticker].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
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

                    curr_close, prev_close = float(close.iloc[-1]), float(close.iloc[-2])
                    curr_high, curr_low = float(high.iloc[-1]), float(low.iloc[-1])
                    curr_vol, curr_vol_sma = float(volume.iloc[-1]), float(vol_sma20.iloc[-1])
                    curr_rsi, curr_atr = float(rsi.iloc[-1]), float(atr.iloc[-1])
                    curr_ema20, curr_ema50, curr_ema200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
                    prev_high20 = float(high_20.iloc[-2])

                    if (curr_close * curr_vol_sma) < 5_000_000:
                        continue

                    clean_symbol = ticker.replace(".NS", "")
                    vol_mult = round(curr_vol / curr_vol_sma, 2) if curr_vol_sma > 0 else 1.0

                    # SWING FILTER
                    if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (vol_mult >= 1.2) and (curr_close >= prev_high20 * 0.99):
                        sl = curr_close - (2.0 * curr_atr)
                        tgt = curr_close + (4.0 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        swing_list.append({"Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty})

                    # INTRADAY FILTER
                    if (curr_close > curr_ema20) and (curr_rsi >= 56) and (vol_mult >= 1.5):
                        sl = curr_close - (1.2 * curr_atr)
                        tgt = curr_close + (2.5 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        intraday_list.append({"Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty})

                    # BTST FILTER
                    day_range = curr_high - curr_low
                    close_loc = (curr_close - curr_low) / day_range if day_range > 0 else 0
                    if (close_loc >= 0.80) and (58 <= curr_rsi <= 75) and (vol_mult >= 1.5) and (curr_close > prev_close):
                        sl = curr_close - (1.5 * curr_atr)
                        tgt = curr_close + (2.0 * curr_atr)
                        qty = int(risk_amount / (curr_close - sl)) if (curr_close - sl) > 0 else 0
                        btst_list.append({"Ticker": clean_symbol, "Price": round(curr_close, 2), "Close_High_%": round(close_loc * 100, 1), "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2), "Qty": qty})

                except Exception:
                    continue

        return {
            "SWING": pd.DataFrame(swing_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if swing_list else pd.DataFrame(),
            "INTRADAY": pd.DataFrame(intraday_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if intraday_list else pd.DataFrame(),
            "BTST": pd.DataFrame(btst_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if btst_list else pd.DataFrame()
        }

    def evaluate_index_options(self, index_symbol="NIFTY"):
        if index_symbol not in VALID_INDEX_SYMBOLS:
            return None

        yf_symbol = INDEX_YF_TICKERS.get(index_symbol, "^NSEI")
        df = yf.download(yf_symbol, period="5d", interval="5m", progress=False)
        if df.empty:
            return None
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df['ema_fast'] = df['Close'].ewm(span=20, min_periods=20).mean()
        df['ema_slow'] = df['Close'].ewm(span=50, min_periods=50).mean()
        df['rsi'] = ta.momentum.rsi(df['Close'], window=14)

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        price, ema_f, ema_s, r = float(latest["Close"]), float(latest["ema_fast"]), float(latest["ema_slow"]), float(latest["rsi"])
        prev_high, prev_low = float(prev["High"]), float(prev["Low"])

        direction = None
        if price > ema_f > ema_s and 45 < r < 70 and price > prev_high:
            direction = "CE"
        elif price < ema_f < ema_s and 30 < r < 55 and price < prev_low:
            direction = "PE"

        if not direction:
            return None

        spot = round(price, 2)
        
        if index_symbol == "NIFTY":
            lot_size = self.lot_sizes["NIFTY"]  # 65
            expiry = nearest_expiry_date(target_weekday=1) # Tuesday weekly expiry
            strike = atm_strike(spot, step=50.0)
            
            try:
                chain = self.nse_opt.fetch_chain(index_symbol)
                spot = spot_price(chain) or spot
                contract = get_contract(chain, strike, expiry, direction)
                entry_premium = contract["ltp"] if (contract and contract.get("ltp")) else round(spot * 0.006, 2)
            except Exception:
                entry_premium = round(spot * 0.006, 2)

        else:  # SENSEX
            lot_size = self.lot_sizes["SENSEX"]  # 20
            expiry = nearest_expiry_date(target_weekday=3) # Thursday weekly expiry
            strike = atm_strike(spot, step=100.0)
            entry_premium = round(spot * 0.005, 2)

        contract_name = f"{index_symbol} {int(strike)} {direction} {expiry}"
        sl_premium = round(entry_premium * (1 - self.opt_stop_loss_pct), 2)
        tgt_premium = round(entry_premium * (1 + self.opt_target_pct), 2)

        risk_amount = self.balance * (self.risk_per_trade_pct / 100.0)
        risk_per_lot = (entry_premium - sl_premium) * lot_size
        lots = int(risk_amount / risk_per_lot) if risk_per_lot > 0 else 1

        return {
            "Contract Symbol": contract_name,
            "Index": index_symbol,
            "Direction": direction,
            "Spot Price": spot,
            "Strike": strike,
            "Expiry": expiry,
            "Premium (LTP)": round(entry_premium, 2),
            "Stop Loss": sl_premium,
            "Target": tgt_premium,
            "Lot Size": lot_size,
            "Recommended Lots": max(1, lots),
            "Total Capital Needed": round(entry_premium * lot_size * max(1, lots), 2)
        }

    def analyze_stock(self, symbol):
        ticker_symbol = f"{symbol.upper()}.NS" if not symbol.endswith(".NS") else symbol.upper()
        df = yf.download(ticker_symbol, period="1y", interval="1d", progress=False)
        if df.empty:
            return {"Error": f"No data found for symbol: {symbol}"}
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
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

        curr_close = float(close.iloc[-1])
        curr_vol = float(volume.iloc[-1])
        curr_vol_sma = float(vol_sma20.iloc[-1])
        curr_rsi = float(rsi.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        curr_ema20 = float(ema20.iloc[-1])
        curr_ema50 = float(ema50.iloc[-1])
        curr_ema200 = float(ema200.iloc[-1])

        c1 = curr_close > curr_ema200
        c2 = curr_ema20 > curr_ema50
        c3 = 50 <= curr_rsi <= 75
        c4 = curr_vol >= curr_vol_sma if curr_vol_sma > 0 else False

        score = sum([c1, c2, c3, c4])

        checklist = [
            f"{'✓' if c1 else '✗'} Price above 200-day EMA (Macro Bullish)",
            f"{'✓' if c2 else '✗'} 20 EMA > 50 EMA (Short-term Uptrend)",
            f"{'✓' if c3 else '✗'} RSI at {round(curr_rsi, 1)} ({'Strong Momentum' if c3 else 'Weak/Overbought'})",
            f"{'✓' if c4 else '✗'} Daily volume above 20-day average"
        ]

        return {
            "Symbol": ticker_symbol.replace(".NS", ""),
            "Price": round(curr_close, 2),
            "Score": f"{score}/4",
            "RSI": round(curr_rsi, 1),
            "ATR": round(curr_atr, 2),
            "EMA200": round(curr_ema200, 2),
            "StopLoss": round(curr_close - (2 * curr_atr), 2),
            "Target": round(curr_close + (4 * curr_atr), 2),
            "Checklist": checklist
        }

    def run(self):
        log.info("Starting background automated execution...")
        universe = self.fetch_nse_universe("NIFTY 500")
        stock_results = self.scan_all_strategies(universe, top_n=5)
        for category, df in stock_results.items():
            if not df.empty:
                log.info(f"[{category}] Detected {len(df)} setups: {df['Ticker'].tolist()}")
            else:
                log.info(f"[{category}] No active setups.")
                
        for idx in self.options_indices:
            try:
                sig = self.evaluate_index_options(idx)
                if sig:
                    log.info(f"[OPTIONS] Signal found for {idx}: {sig['Contract Symbol']}")
                else:
                    log.info(f"[OPTIONS] No active setup for {idx}")
            except Exception as e:
                log.error(f"[OPTIONS] Failed evaluating {idx}: {e}")
                
        log.info("Background execution finished successfully.")
