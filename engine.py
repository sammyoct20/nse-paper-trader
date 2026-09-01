import time
import requests
import pandas as pd
import numpy as np
import ta
import logging

log = logging.getLogger("unified_engine")

class NSELiveClient:
    """
    Direct client that fetches live quotes and 1-year daily historical candle data
    directly from official NSE India backend endpoints.
    """
    BASE_URL = "https://www.nseindia.com"
    QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol="
    HISTORICAL_URL = "https://www.nseindia.com/api/historical/cm/equity?symbol="

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/get-quotes/equity"
        })
        self._refresh_cookies()

    def _refresh_cookies(self):
        """Refreshes official NSE session cookies."""
        try:
            self.session.get(self.BASE_URL, timeout=10)
        except Exception as e:
            log.warning(f"Failed to refresh NSE session: {e}")

    def fetch_stock_history(self, symbol: str) -> pd.DataFrame:
        """Fetches 1-year daily historical data directly from official NSE endpoints."""
        clean_symbol = symbol.replace(".NS", "").upper()
        url = f"{self.HISTORICAL_URL}{clean_symbol}"
        
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code in (401, 403):
                self._refresh_cookies()
                response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                return pd.DataFrame()

            raw_data = response.json().get('data', [])
            if not raw_data:
                return pd.DataFrame()

            df = pd.DataFrame(raw_data)
            df['Date'] = pd.to_datetime(df['CH_TIMESTAMP'])
            df = df.sort_values('Date').reset_index(drop=True)

            # Map NSE column structure to engine layout
            df['Open'] = pd.to_numeric(df['CH_OPENING_PRICE'], errors='coerce')
            df['High'] = pd.to_numeric(df['CH_TRADE_HIGH_PRICE'], errors='coerce')
            df['Low'] = pd.to_numeric(df['CH_TRADE_LOW_PRICE'], errors='coerce')
            df['Close'] = pd.to_numeric(df['CH_CLOSING_PRICE'], errors='coerce')
            df['Volume'] = pd.to_numeric(df['CH_TOT_TRADED_QTY'], errors='coerce')

            return df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna()

        except Exception as e:
            log.warning(f"Error fetching official NSE history for {clean_symbol}: {e}")
            return pd.DataFrame()


def scan_all_strategies(self, tickers=None, top_n=5, paper_trade=False):
    """
    Executes all strategy scans using official live NSE website data.
    """
    if tickers is None:
        tickers = self.fetch_nse_universe("NIFTY 500")[cite: 2]

    status = self.risk_status()[cite: 2]
    open_slots = max(0, self.risk.max_open_positions - status["open_positions"]) if paper_trade else None[cite: 2]
    
    nse_client = NSELiveClient()
    swing_list, intraday_list, btst_list = [], [], []

    for ticker in tickers:
        try:
            # Fetch data directly from NSE India instead of yfinance
            df = nse_client.fetch_stock_history(ticker)
            
            if len(df) < 50:
                continue[cite: 2]

            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']

            # All original technical indicators calculated without changes
            ema20 = ta.trend.ema_indicator(close, window=20)[cite: 2]
            ema50 = ta.trend.ema_indicator(close, window=50)[cite: 2]
            ema200 = ta.trend.ema_indicator(close, window=200)[cite: 2]
            rsi = ta.momentum.rsi(close, window=14)[cite: 2]
            atr = ta.volatility.average_true_range(high, low, close, window=14)[cite: 2]
            vol_sma20 = volume.rolling(20).mean()[cite: 2]
            high_20 = high.rolling(20).max()[cite: 2]

            curr_close, prev_close = float(close.iloc[-1]), float(close.iloc[-2])[cite: 2]
            curr_high, curr_low = float(high.iloc[-1]), float(low.iloc[-1])[cite: 2]
            curr_vol, curr_vol_sma = float(volume.iloc[-1]), float(vol_sma20.iloc[-1])[cite: 2]
            curr_rsi, curr_atr = float(rsi.iloc[-1]), float(atr.iloc[-1])[cite: 2]
            curr_ema20, curr_ema50, curr_ema200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])[cite: 2]
            prev_high20 = float(high_20.iloc[-2])[cite: 2]

            # Minimum Liquidity Check
            if (curr_close * curr_vol_sma) < 5_000_000:
                continue[cite: 2]

            clean_symbol = ticker.replace(".NS", "")[cite: 2]
            vol_mult = round(curr_vol / curr_vol_sma, 2) if curr_vol_sma > 0 else 1.0[cite: 2]

            # -----------------------------------------------------------------
            # 1. SWING STRATEGY (INTACT)
            # -----------------------------------------------------------------
            if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (vol_mult >= 1.2) and (curr_close >= prev_high20 * 0.99):[cite: 2]
                sl = curr_close - (1.5 * curr_atr)[cite: 2]
                tgt = curr_close + (2.5 * curr_atr)[cite: 2]
                
                if self.risk.passes_reward_risk(curr_close, sl, tgt):[cite: 2]
                    pos = self.risk.position_size(curr_close, sl)[cite: 2]
                    qty = pos.qty if status["new_entries_allowed"] else 0[cite: 2]
                    
                    traded = self._maybe_open_stock(paper_trade, "SWING", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)[cite: 2]
                    if traded and open_slots is not None:
                        open_slots -= 1[cite: 2]

                    swing_list.append({
                        "Ticker": clean_symbol,
                        "Price": round(curr_close, 2),
                        "RSI": round(curr_rsi, 1),
                        "Vol_Mult": vol_mult,
                        "StopLoss": round(sl, 2),
                        "Target": round(tgt, 2),
                        "Qty": qty,
                        "Risk_Amt": pos.risk_amount,
                        "Position_Val": pos.position_value,
                        **({"Traded": traded} if paper_trade else {})
                    })[cite: 2]

            # -----------------------------------------------------------------
            # 2. INTRADAY STRATEGY (INTACT)
            # -----------------------------------------------------------------
            if (curr_close > curr_ema20) and (curr_rsi >= 56) and (vol_mult >= 1.5):[cite: 2]
                sl = curr_close - (1.0 * curr_atr)[cite: 2]
                tgt = curr_close + (1.8 * curr_atr)[cite: 2]
                
                if self.risk.passes_reward_risk(curr_close, sl, tgt):[cite: 2]
                    pos = self.risk.position_size(curr_close, sl)[cite: 2]
                    qty = pos.qty if status["new_entries_allowed"] else 0[cite: 2]
                    
                    traded = self._maybe_open_stock(paper_trade, "INTRADAY", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)[cite: 2]
                    if traded and open_slots is not None:
                        open_slots -= 1[cite: 2]

                    intraday_list.append({
                        "Ticker": clean_symbol,
                        "Price": round(curr_close, 2),
                        "RSI": round(curr_rsi, 1),
                        "Vol_Mult": vol_mult,
                        "StopLoss": round(sl, 2),
                        "Target": round(tgt, 2),
                        "Qty": qty,
                        "Risk_Amt": pos.risk_amount,
                        "Position_Val": pos.position_value,
                        **({"Traded": traded} if paper_trade else {})
                    })[cite: 2]

            # -----------------------------------------------------------------
            # 3. BTST STRATEGY (INTACT)
            # -----------------------------------------------------------------
            day_range = curr_high - curr_low[cite: 2]
            close_loc = (curr_close - curr_low) / day_range if day_range > 0 else 0[cite: 2]

            if (close_loc >= 0.80) and (58 <= curr_rsi <= 75) and (vol_mult >= 1.5) and (curr_close > prev_close):[cite: 2]
                sl = curr_close - (1.3 * curr_atr)[cite: 2]
                tgt = curr_close + (2.0 * curr_atr)[cite: 2]
                
                if self.risk.passes_reward_risk(curr_close, sl, tgt):[cite: 2]
                    pos = self.risk.position_size(curr_close, sl)[cite: 2]
                    qty = pos.qty if status["new_entries_allowed"] else 0[cite: 2]
                    
                    traded = self._maybe_open_stock(paper_trade, "BTST", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)[cite: 2]
                    if traded and open_slots is not None:
                        open_slots -= 1[cite: 2]

                    btst_list.append({
                        "Ticker": clean_symbol,
                        "Price": round(curr_close, 2),
                        "Close_High_%": round(close_loc * 100, 1),
                        "RSI": round(curr_rsi, 1),
                        "Vol_Mult": vol_mult,
                        "StopLoss": round(sl, 2),
                        "Target": round(tgt, 2),
                        "Qty": qty,
                        "Risk_Amt": pos.risk_amount,
                        "Position_Val": pos.position_value,
                        **({"Traded": traded} if paper_trade else {})
                    })[cite: 2]

            # Rate-limiting pause to respect NSE servers
            time.sleep(0.1)

        except Exception:
            continue

    return {
        "SWING": pd.DataFrame(swing_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if swing_list else pd.DataFrame(),[cite: 2]
        "INTRADAY": pd.DataFrame(intraday_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if intraday_list else pd.DataFrame(),[cite: 2]
        "BTST": pd.DataFrame(btst_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if btst_list else pd.DataFrame()[cite: 2]
    }
