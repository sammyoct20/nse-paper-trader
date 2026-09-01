import time
import requests
import pandas as pd
import numpy as np
import ta
import logging
from datetime import datetime

log = logging.getLogger("unified_engine")

class NSELiveClient:
    """
    Direct client that fetches live quotes and 1-year historical daily candles 
    from official NSE India endpoints.
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


class PositionSizeResult:
    def __init__(self, qty, risk_amount, position_value):
        self.qty = qty
        self.risk_amount = risk_amount
        self.position_value = position_value


class RiskManager:
    """
    Manages risk allocation parameters consumed by app.py.
    """
    def __init__(self, capital=100000.0, risk_per_trade_pct=1.0, max_open_positions=5, min_rr_ratio=1.5):
        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.min_rr_ratio = min_rr_ratio

    def passes_reward_risk(self, entry, sl, tgt):
        risk = abs(entry - sl)
        reward = abs(tgt - entry)
        if risk <= 0:
            return False
        return (reward / risk) >= self.min_rr_ratio

    def position_size(self, entry, sl):
        risk_per_share = abs(entry - sl)
        if risk_per_share <= 0:
            return PositionSizeResult(0, 0.0, 0.0)

        risk_amount = self.capital * (self.risk_per_trade_pct / 100.0)
        qty = int(risk_amount // risk_per_share)
        
        # Capital cap per trade check (e.g., max 20% capital in single stock)
        max_position_val = self.capital * 0.20
        if (qty * entry) > max_position_val:
            qty = int(max_position_val // entry)

        position_value = round(qty * entry, 2)
        actual_risk = round(qty * risk_per_share, 2)

        return PositionSizeResult(qty, actual_risk, position_value)


class PaperEngine:
    """
    Core Trading Engine consumed by Streamlit app.py
    """
    def __init__(self, capital=100000.0, risk_per_trade_pct=1.0, max_open_positions=5):
        self.risk = RiskManager(
            capital=capital,
            risk_per_trade_pct=risk_per_trade_pct,
            max_open_positions=max_open_positions
        )
        self.open_positions = []
        self.nse_client = NSELiveClient()

    def risk_status(self):
        """Returns risk and position allocation metrics expected by app.py."""
        return {
            "capital": self.risk.capital,
            "risk_per_trade_pct": self.risk.risk_per_trade_pct,
            "risk_per_trade_amount": self.risk.capital * (self.risk.risk_per_trade_pct / 100.0),
            "max_open_positions": self.risk.max_open_positions,
            "open_positions": len(self.open_positions),
            "new_entries_allowed": len(self.open_positions) < self.risk.max_open_positions
        }

    def fetch_nse_universe(self, index_name="NIFTY 50"):
        """Default universe listing."""
        return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"]

    def _maybe_open_stock(self, paper_trade, strategy, symbol, close, sl, tgt, qty, risk_amt, pos_val, status, open_slots):
        """Simulates paper trading order entry."""
        if paper_trade and open_slots is not None and open_slots > 0:
            trade = {
                "strategy": strategy,
                "symbol": symbol,
                "entry_price": close,
                "current_price": close,
                "sl": sl,
                "tgt": tgt,
                "qty": qty,
                "risk_amount": risk_amt,
                "position_value": pos_val,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.open_positions.append(trade)
            return True
        return False

    def _close_position(self, trade, exit_price, reason="EOD Market Close"):
        """Removes an active position on exit trigger."""
        if trade in self.open_positions:
            self.open_positions.remove(trade)
            log.info(f"Position closed for {trade['symbol']} | Reason: {reason} | Exit Price: {exit_price}")

    def close_expired_intraday_trades(self):
        """
        Auto-closes open INTRADAY paper positions if current time is past 15:15 IST.
        """
        now = datetime.now()
        if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
            for trade in list(self.open_positions):
                if trade.get("strategy") == "INTRADAY":
                    current_price = trade.get("current_price", trade.get("entry_price"))
                    self._close_position(trade, exit_price=current_price, reason="EOD Market Close")

    def scan_all_strategies(self, tickers=None, top_n=5, paper_trade=False):
        """
        Executes strategy scans using official live NSE website data.
        """
        self.close_expired_intraday_trades()

        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 50")

        status = self.risk_status()
        open_slots = max(0, self.risk.max_open_positions - status["open_positions"]) if paper_trade else None

        swing_list, intraday_list, btst_list = [], [], []

        for ticker in tickers:
            try:
                df = self.nse_client.fetch_stock_history(ticker)
                
                if len(df) < 50:
                    continue

                close = df['Close']
                high = df['High']
                low = df['Low']
                volume = df['Volume']

                # Technical Indicators
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

                # 1. SWING STRATEGY
                if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (vol_mult >= 1.2) and (curr_close >= prev_high20 * 0.99):
                    sl = curr_close - (1.5 * curr_atr)
                    tgt = curr_close + (2.5 * curr_atr)
                    
                    if self.risk.passes_reward_risk(curr_close, sl, tgt):
                        pos = self.risk.position_size(curr_close, sl)
                        qty = pos.qty if status["new_entries_allowed"] else 0
                        
                        traded = self._maybe_open_stock(paper_trade, "SWING", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)
                        if traded and open_slots is not None:
                            open_slots -= 1

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
                        })

                # 2. INTRADAY STRATEGY
                if (curr_close > curr_ema20) and (curr_rsi >= 56) and (vol_mult >= 1.5):
                    sl = curr_close - (1.0 * curr_atr)
                    tgt = curr_close + (1.8 * curr_atr)
                    
                    if self.risk.passes_reward_risk(curr_close, sl, tgt):
                        pos = self.risk.position_size(curr_close, sl)
                        qty = pos.qty if status["new_entries_allowed"] else 0
                        
                        traded = self._maybe_open_stock(paper_trade, "INTRADAY", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)
                        if traded and open_slots is not None:
                            open_slots -= 1

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
                        })

                # 3. BTST STRATEGY
                day_range = curr_high - curr_low
                close_loc = (curr_close - curr_low) / day_range if day_range > 0 else 0

                if (close_loc >= 0.80) and (58 <= curr_rsi <= 75) and (vol_mult >= 1.5) and (curr_close > prev_close):
                    sl = curr_close - (1.3 * curr_atr)
                    tgt = curr_close + (2.0 * curr_atr)
                    
                    if self.risk.passes_reward_risk(curr_close, sl, tgt):
                        pos = self.risk.position_size(curr_close, sl)
                        qty = pos.qty if status["new_entries_allowed"] else 0
                        
                        traded = self._maybe_open_stock(paper_trade, "BTST", clean_symbol, curr_close, sl, tgt, qty, pos.risk_amount, pos.position_value, status, open_slots)
                        if traded and open_slots is not None:
                            open_slots -= 1

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
                        })

                time.sleep(0.1)

            except Exception:
                continue

        return {
            "SWING": pd.DataFrame(swing_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if swing_list else pd.DataFrame(),
            "INTRADAY": pd.DataFrame(intraday_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if intraday_list else pd.DataFrame(),
            "BTST": pd.DataFrame(btst_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if btst_list else pd.DataFrame()
        }
