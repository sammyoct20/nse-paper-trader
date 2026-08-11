import yfinance as yf
import pandas as pd
import psycopg2
import os
import time
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("paper_engine")


class PaperEngine:

    def __init__(self):
        self.dsn = os.getenv("DATABASE_URL")
        if not self.dsn:
            raise RuntimeError("DATABASE_URL env var is not set")

        self.conn = None
        self._connect_db()

        # ---- config (env-overridable so you can tune without code changes) ----
        self.capital = float(os.getenv("CAPITAL", 100000))
        self.max_trades = int(os.getenv("MAX_TRADES", 3))
        self.risk_per_trade_pct = float(os.getenv("RISK_PER_TRADE_PCT", 0.01))  # 1% of capital risked per trade
        self.atr_period = int(os.getenv("ATR_PERIOD", 14))
        self.atr_stop_mult = float(os.getenv("ATR_STOP_MULT", 1.5))
        self.atr_target_mult = float(os.getenv("ATR_TARGET_MULT", 3.0))  # ~2:1 reward:risk
        self.volume_mult = float(os.getenv("VOLUME_MULT", 1.5))
        self.rsi_period = int(os.getenv("RSI_PERIOD", 14))
        self.rsi_max = float(os.getenv("RSI_MAX", 70))  # avoid chasing overbought breakouts
        self.min_price = float(os.getenv("MIN_PRICE", 20))  # skip illiquid penny-priced names
        self.max_retries = int(os.getenv("FETCH_RETRIES", 2))

        # Nifty stocks (you can expand later)
        self.symbols = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
            "LT.NS", "SBIN.NS", "ITC.NS", "AXISBANK.NS", "KOTAKBANK.NS"
        ]

        self.ensure_schema()

    # ----------------------------------
    # DB CONNECTION (with reconnect)
    # ----------------------------------
    def _connect_db(self):
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = False

    def _cursor(self):
        """Return a live cursor, reconnecting to the DB first if the connection has died."""
        try:
            if self.conn.closed:
                raise psycopg2.OperationalError("connection closed")
            cur = self.conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            return self.conn.cursor()
        except psycopg2.OperationalError:
            log.warning("DB connection lost, reconnecting...")
            self._connect_db()
            return self.conn.cursor()

    # ----------------------------------
    # AUTO FIX DB SCHEMA (CRITICAL)
    # ----------------------------------
    def ensure_schema(self):
        cur = self._cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            entry FLOAT,
            stop_loss FLOAT,
            target FLOAT,
            qty INT,
            status TEXT DEFAULT 'OPEN',
            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exit_time TIMESTAMP,
            exit_reason TEXT,
            pnl FLOAT
        );
        """)

        # Ensure missing columns (safe updates)
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN';")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS target FLOAT;")
        # index to make the duplicate-position check and open-trade lookups fast
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_status_symbol ON trades(status, symbol);")

        self.conn.commit()
        cur.close()

    # ----------------------------------
    # FETCH DATA (retries + normalizes yfinance's column shape)
    # ----------------------------------
    def get_data(self, symbol, period="5d", interval="5m"):
        for attempt in range(1, self.max_retries + 2):
            try:
                df = yf.download(
                    symbol, period=period, interval=interval,
                    progress=False, auto_adjust=True,
                )

                if df is None or df.empty:
                    return None

                # yfinance sometimes returns MultiIndex columns (e.g. ('Close','RELIANCE.NS'))
                # even for a single symbol depending on version - flatten to plain columns.
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                needed = {"Open", "High", "Low", "Close", "Volume"}
                if not needed.issubset(df.columns):
                    log.warning(f"{symbol}: missing expected columns, got {list(df.columns)}")
                    return None

                df = df.dropna(subset=["Close", "Volume"])

                if len(df) < max(self.atr_period, 50) + 2:
                    return None

                return df

            except Exception as e:
                log.warning(f"{symbol}: fetch attempt {attempt} failed: {e}")
                time.sleep(1)

        return None

    # ----------------------------------
    # INDICATORS
    # ----------------------------------
    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high, low, close = df["High"], df["Low"], df["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, min_periods=period).mean()

    # ----------------------------------
    # STRATEGY (trend + momentum + volatility-aware sizing)
    # ----------------------------------
    def generate_signal(self, sym, open_symbols):
        # never open a second position in a symbol we already hold
        if sym in open_symbols:
            return None

        df = self.get_data(sym)
        if df is None:
            return None

        df["ema20"] = df["Close"].ewm(span=20, min_periods=20).mean()
        df["ema50"] = df["Close"].ewm(span=50, min_periods=50).mean()
        df["rsi"] = self._rsi(df["Close"], self.rsi_period)
        df["atr"] = self._atr(df, self.atr_period)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price = float(latest["Close"])
        ema20 = float(latest["ema20"])
        ema50 = float(latest["ema50"])
        rsi = float(latest["rsi"])
        atr = float(latest["atr"])
        volume = float(latest["Volume"])
        avg_volume = float(df["Volume"].tail(20).mean())

        if pd.isna(ema20) or pd.isna(ema50) or pd.isna(atr) or atr <= 0:
            return None

        if price < self.min_price:
            return None

        # trend filter: price above both EMAs, EMAs stacked in bullish order
        if not (price > ema20 > ema50):
            return None

        # momentum confirmation, but avoid entries already deep into overbought territory
        if not (30 < rsi < self.rsi_max):
            return None

        # volume confirmation: this move has real participation
        if avg_volume <= 0 or volume < self.volume_mult * avg_volume:
            return None

        # breakout confirmation vs prior candle
        if price <= float(prev["High"]):
            return None

        stop_loss = price - self.atr_stop_mult * atr
        target = price + self.atr_target_mult * atr

        if stop_loss <= 0 or stop_loss >= price:
            return None

        # position size from risk budget, not just capital / N
        risk_amount = self.capital * self.risk_per_trade_pct
        risk_per_share = price - stop_loss
        qty_by_risk = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0

        # cap by an equal per-slot capital allocation too, so one trade can't eat the whole book
        qty_by_capital = int((self.capital / self.max_trades) / price)

        qty = max(0, min(qty_by_risk, qty_by_capital))
        if qty < 1:
            return None

        return {
            "symbol": sym,
            "entry": round(price, 2),
            "stop_loss": round(stop_loss, 2),
            "target": round(target, 2),
            "qty": qty,
        }

    # ----------------------------------
    # SAVE TRADE
    # ----------------------------------
    def save_trade(self, trade):
        cur = self._cursor()
        try:
            cur.execute("""
            INSERT INTO trades (symbol, entry, stop_loss, target, qty, status, entry_time)
            VALUES (%s,%s,%s,%s,%s,'OPEN',CURRENT_TIMESTAMP)
            """, (
                trade["symbol"],
                trade["entry"],
                trade["stop_loss"],
                trade["target"],
                trade["qty"],
            ))
            self.conn.commit()
            log.info(f"Opened {trade['symbol']} qty={trade['qty']} entry={trade['entry']} "
                     f"sl={trade['stop_loss']} target={trade['target']}")
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    # ----------------------------------
    # GET OPEN TRADES
    # ----------------------------------
    def get_open_trades(self):
        cur = self._cursor()
        cur.execute("SELECT id, symbol, entry, stop_loss, target, qty FROM trades WHERE status='OPEN'")
        rows = cur.fetchall()
        cur.close()
        return rows

    # ----------------------------------
    # UPDATE TRADES (AUTO CLOSE)
    # ----------------------------------
    def update_trades(self):
        trades = self.get_open_trades()
        if not trades:
            return

        cur = self._cursor()
        try:
            for t in trades:
                trade_id, sym, entry, sl, target, qty = t

                df = self.get_data(sym, period="1d", interval="5m")
                if df is None or df.empty:
                    log.warning(f"{sym}: no data to evaluate open trade {trade_id}, skipping this cycle")
                    continue

                price = float(df["Close"].iloc[-1])

                exit_reason = None
                if price <= sl:
                    exit_reason = "STOP LOSS"
                elif price >= target:
                    exit_reason = "TARGET HIT"

                if exit_reason:
                    pnl = round((price - entry) * qty, 2)
                    log.info(f"Closing {sym} (#{trade_id}) | {exit_reason} | pnl={pnl}")

                    cur.execute("""
                    UPDATE trades
                    SET status='CLOSED',
                        exit_time=CURRENT_TIMESTAMP,
                        exit_reason=%s,
                        pnl=%s
                    WHERE id=%s
                    """, (exit_reason, pnl, trade_id))

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    # ----------------------------------
    # MAIN RUN
    # ----------------------------------
    def run_once(self):
        self.update_trades()

        open_trades = self.get_open_trades()
        open_symbols = {t[1] for t in open_trades}
        slots = self.max_trades - len(open_trades)

        if slots <= 0:
            log.info("No free slots, skipping new entries this cycle")
            return [], 0

        signals = []
        trades_created = 0

        for sym in self.symbols:
            try:
                signal = self.generate_signal(sym, open_symbols)
            except Exception as e:
                log.error(f"{sym}: signal generation failed: {e}")
                continue

            if signal:
                try:
                    self.save_trade(signal)
                except Exception as e:
                    log.error(f"{sym}: failed to save trade: {e}")
                    continue

                signals.append(signal)
                open_symbols.add(sym)
                trades_created += 1
                slots -= 1

            if slots == 0:
                break

        return signals, trades_created

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()


if __name__ == "__main__":
    engine = PaperEngine()
    try:
        signals, created = engine.run_once()
        log.info(f"Run complete: {created} new trade(s) opened")
    finally:
        engine.close()
