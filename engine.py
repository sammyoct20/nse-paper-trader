import yfinance as yf
import pandas as pd
import psycopg2
import os
import time
import logging

from strategy_core import StrategyConfig, compute_indicators, evaluate_row

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

        # ---- strategy config (env-overridable) ----
        self.cfg = StrategyConfig(
            capital=float(os.getenv("CAPITAL", 100000)),
            max_trades=int(os.getenv("MAX_TRADES", 3)),
            risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", 0.01)),
            atr_period=int(os.getenv("ATR_PERIOD", 14)),
            atr_stop_mult=float(os.getenv("ATR_STOP_MULT", 1.5)),
            atr_target_mult=float(os.getenv("ATR_TARGET_MULT", 3.0)),
            volume_mult=float(os.getenv("VOLUME_MULT", 1.5)),
            rsi_period=int(os.getenv("RSI_PERIOD", 14)),
            rsi_min=float(os.getenv("RSI_MIN", 30)),
            rsi_max=float(os.getenv("RSI_MAX", 70)),
            min_price=float(os.getenv("MIN_PRICE", 20)),
        )
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

        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN';")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss FLOAT;")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS target FLOAT;")
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

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                needed = {"Open", "High", "Low", "Close", "Volume"}
                if not needed.issubset(df.columns):
                    log.warning(f"{symbol}: missing expected columns, got {list(df.columns)}")
                    return None

                df = df.dropna(subset=["Close", "Volume"])

                if len(df) < max(self.cfg.atr_period, self.cfg.ema_slow) + 2:
                    return None

                return df

            except Exception as e:
                log.warning(f"{symbol}: fetch attempt {attempt} failed: {e}")
                time.sleep(1)

        return None

    # ----------------------------------
    # STRATEGY (delegates to strategy.py so live + backtest never drift)
    # ----------------------------------
    def generate_signal(self, sym, open_symbols):
        if sym in open_symbols:
            return None

        df = self.get_data(sym)
        if df is None:
            return None

        df = compute_indicators(df, self.cfg)
        sig = evaluate_row(df, len(df) - 1, self.cfg, capital_for_sizing=self.cfg.capital)
        if sig is None:
            return None

        sig["symbol"] = sym
        return sig

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
        slots = self.cfg.max_trades - len(open_trades)

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
