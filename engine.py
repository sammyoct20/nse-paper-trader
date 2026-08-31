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

from risk_manager import RiskManager

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
        # Case-insensitive expiry match: we store expiry as "04-SEP-2025"
        # (nearest_expiry_date() upper-cases it) but NSE's live chain
        # returns "04-Sep-2025" (mixed case), so a plain == comparison
        # silently never matched — every option lookup fell through to
        # the entry_premium fallback, and the same lookup also failed
        # when checking whether to auto-close the position.
        if row.get("strikePrice") == strike and str(row.get("expiryDate", "")).upper() == str(expiry).upper():
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
# SCHEMA — single source of truth. ensure_schema() creates each table with
# just an id, then runs every (column, postgres_type, sqlite_type) tuple
# below through an ALTER TABLE ... ADD COLUMN pass every startup. This makes
# schema self-healing: if a table already exists in production (e.g. from
# an earlier deployment) but is missing newer columns, it gets patched up
# instead of silently staying stale, since CREATE TABLE IF NOT EXISTS is a
# no-op on an existing table and won't add anything by itself.
# -------------------------------------------------------------------
OPTIONS_TRADES_COLUMNS = [
    ("contract_name", "TEXT", "TEXT"),
    ("index_symbol", "TEXT", "TEXT"),
    ("option_type", "TEXT", "TEXT"),
    ("strike", "FLOAT", "REAL"),
    ("expiry", "TEXT", "TEXT"),
    ("lot_size", "INT", "INTEGER"),
    ("lots", "INT", "INTEGER"),
    ("entry_premium", "FLOAT", "REAL"),
    ("stop_loss_premium", "FLOAT", "REAL"),
    ("target_premium", "FLOAT", "REAL"),
    ("risk_amount", "FLOAT", "REAL"),
    ("position_value", "FLOAT", "REAL"),
    ("status", "TEXT DEFAULT 'OPEN'", "TEXT DEFAULT 'OPEN'"),
    # No DEFAULT CURRENT_TIMESTAMP here: SQLite refuses to ADD COLUMN with a
    # non-constant default on a table that already has rows ("Cannot add a
    # column with non-constant default"). entry_time/updated_at are set
    # explicitly in every INSERT/UPDATE below instead.
    ("entry_time", "TIMESTAMP", "TIMESTAMP"),
    ("exit_time", "TIMESTAMP", "TIMESTAMP"),
    ("exit_premium", "FLOAT", "REAL"),
    ("exit_reason", "TEXT", "TEXT"),
    ("pnl", "FLOAT", "REAL"),
]

STOCK_TRADES_COLUMNS = [
    ("ticker", "TEXT", "TEXT"),
    ("strategy", "TEXT", "TEXT"),
    ("entry_price", "FLOAT", "REAL"),
    ("stop_loss", "FLOAT", "REAL"),
    ("target", "FLOAT", "REAL"),
    ("qty", "INT", "INTEGER"),
    ("risk_amount", "FLOAT", "REAL"),
    ("position_value", "FLOAT", "REAL"),
    ("status", "TEXT DEFAULT 'OPEN'", "TEXT DEFAULT 'OPEN'"),
    ("entry_time", "TIMESTAMP", "TIMESTAMP"),  # set explicitly on INSERT — see note above
    ("exit_time", "TIMESTAMP", "TIMESTAMP"),
    ("exit_price", "FLOAT", "REAL"),
    ("exit_reason", "TEXT", "TEXT"),
    ("pnl", "FLOAT", "REAL"),
]

ACCOUNT_STATE_COLUMNS = [
    ("capital", "FLOAT", "REAL"),
    ("updated_at", "TIMESTAMP", "TIMESTAMP"),  # set explicitly on INSERT/UPDATE — see note above
]

# -------------------------------------------------------------------
# MAIN CORE ENGINE: PAPER ENGINE (STOCKS & OPTIONS)
# -------------------------------------------------------------------
class PaperEngine:
    def __init__(self, initial_balance=100000.0, risk_per_trade_pct=1.0):
        self.risk_per_trade_pct = risk_per_trade_pct
        env_initial_balance = float(os.getenv("OPTIONS_CAPITAL", initial_balance))

        self.db_dsn = os.getenv("DATABASE_URL")
        self.ph = "%s" if self.db_dsn else "?"  # SQL parameter placeholder style
        self.ensure_schema()

        # Capital is persisted in account_state and reloaded here, so it
        # survives across process restarts (important since GitHub Actions
        # spins up a brand-new process every 5 minutes — an in-memory
        # balance would otherwise reset every run instead of compounding
        # with real paper P&L).
        self.balance = self._load_or_init_balance(env_initial_balance)

        # ---- Kotegawa-style risk management ----------------------------
        # Risk a small fixed % of capital per trade, cap any single
        # position's capital allocation, cap simultaneous open positions,
        # and halt new entries once a daily loss limit is hit. See
        # risk_manager.py for the rationale behind each parameter.
        self.risk = RiskManager(
            capital=self.balance,
            risk_per_trade_pct=self.risk_per_trade_pct,
        )

        # Options Config (NIFTY: 65, SENSEX: 20)
        self.options_indices = ["NIFTY", "SENSEX"]
        self.lot_sizes = {
            "NIFTY": int(os.getenv("NIFTY_LOT_SIZE", 65)),
            "SENSEX": int(os.getenv("SENSEX_LOT_SIZE", 20))
        }
        # Tighter than before by design: options premiums move fast, and
        # Kotegawa's edge came from cutting losses quickly rather than
        # hoping a losing trade recovers.
        self.opt_stop_loss_pct = float(os.getenv("OPTIONS_STOP_LOSS_PCT", 0.25))
        self.opt_target_pct = float(os.getenv("OPTIONS_TARGET_PCT", 0.40))

        self.nse_opt = NSEOptionsClient()

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
            cur.execute("CREATE TABLE IF NOT EXISTS options_trades (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE IF NOT EXISTS stock_trades (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE IF NOT EXISTS account_state (id INT PRIMARY KEY)")
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS options_trades (id INTEGER PRIMARY KEY AUTOINCREMENT)")
            cur.execute("CREATE TABLE IF NOT EXISTS stock_trades (id INTEGER PRIMARY KEY AUTOINCREMENT)")
            cur.execute("CREATE TABLE IF NOT EXISTS account_state (id INTEGER PRIMARY KEY)")
        conn.commit()

        # Every table is created with just an id above, then every expected
        # column is patched in here. This runs on every startup and is a
        # no-op once columns exist, but it means a pre-existing production
        # table missing newer columns (from an earlier deployment) gets
        # healed automatically instead of throwing "column does not exist"
        # at query time.
        self._ensure_columns(cur, conn, "options_trades", OPTIONS_TRADES_COLUMNS)
        self._ensure_columns(cur, conn, "stock_trades", STOCK_TRADES_COLUMNS)
        self._ensure_columns(cur, conn, "account_state", ACCOUNT_STATE_COLUMNS)

        conn.close()

    def _ensure_columns(self, cur, conn, table: str, columns: list):
        for col, pg_type, sqlite_type in columns:
            if self.db_dsn:
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_type}")
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    log.warning(f"Could not ensure column {table}.{col}: {e}")
            else:
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sqlite_type}")
                    conn.commit()
                except Exception:
                    pass  # column already exists — sqlite has no IF NOT EXISTS for ADD COLUMN

    # -----------------------------------------------------------------
    # ACCOUNT CAPITAL — persisted so it survives restarts across cron runs
    # -----------------------------------------------------------------
    def _load_or_init_balance(self, initial_balance: float) -> float:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT capital FROM account_state WHERE id = 1")
        row = cur.fetchone()
        reset = os.getenv("RESET_CAPITAL_ON_START", "false").lower() == "true"

        if row is None:
            capital = float(initial_balance)
            cur.execute(f"INSERT INTO account_state (id, capital, updated_at) VALUES (1, {self.ph}, CURRENT_TIMESTAMP)", (capital,))
            conn.commit()
        elif reset:
            capital = float(initial_balance)
            cur.execute(f"UPDATE account_state SET capital = {self.ph}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (capital,))
            conn.commit()
        else:
            capital = float(row[0])

        conn.close()
        return capital

    def _persist_balance(self):
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(f"UPDATE account_state SET capital = {self.ph}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (self.balance,))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Could not persist updated capital: {e}")

    def _apply_pnl(self, pnl: float):
        self.balance += pnl
        self._persist_balance()
        self.risk.capital = self.balance  # subsequent sizing in this run uses the fresh balance

    # -----------------------------------------------------------------
    # RISK STATUS / CIRCUIT BREAKER HELPERS (portfolio-wide: stocks + options)
    # -----------------------------------------------------------------
    def get_open_positions_count(self) -> int:
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM options_trades WHERE status = 'OPEN'")
            n = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM stock_trades WHERE status = 'OPEN'")
            n += cur.fetchone()[0] or 0
            conn.close()
            return int(n)
        except Exception as e:
            log.warning(f"Could not read open positions count: {e}")
            return 0

    def get_daily_realized_pnl(self) -> float:
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            if self.db_dsn:
                cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM options_trades WHERE status = 'CLOSED' AND exit_time::date = CURRENT_DATE")
                total = cur.fetchone()[0] or 0
                cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM stock_trades WHERE status = 'CLOSED' AND exit_time::date = CURRENT_DATE")
                total += cur.fetchone()[0] or 0
            else:
                cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM options_trades WHERE status = 'CLOSED' AND DATE(exit_time) = DATE('now')")
                total = cur.fetchone()[0] or 0
                cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM stock_trades WHERE status = 'CLOSED' AND DATE(exit_time) = DATE('now')")
                total += cur.fetchone()[0] or 0
            conn.close()
            return float(total)
        except Exception as e:
            log.warning(f"Could not read daily realized PnL: {e}")
            return 0.0

    def risk_status(self) -> dict:
        """Snapshot of the account-level risk state, used by the dashboard
        and by scan/signal methods to decide whether new entries are allowed."""
        daily_pnl = self.get_daily_realized_pnl()
        open_positions = self.get_open_positions_count()
        breaker = self.risk.circuit_breaker_tripped(daily_pnl)
        positions_ok = self.risk.open_positions_allowed(open_positions)
        return {
            "capital": round(self.balance, 2),
            "risk_per_trade_pct": self.risk.risk_per_trade_pct,
            "risk_per_trade_amount": round(self.risk.risk_amount(), 2),
            "max_position_pct": self.risk.max_position_pct,
            "max_position_value": round(self.risk.max_position_value(), 2),
            "max_daily_loss_pct": self.risk.max_daily_loss_pct,
            "daily_loss_limit": round(self.risk.daily_loss_limit(), 2),
            "daily_realized_pnl": round(daily_pnl, 2),
            "open_positions": open_positions,
            "max_open_positions": self.risk.max_open_positions,
            "circuit_breaker_tripped": breaker,
            "new_entries_allowed": (not breaker) and positions_ok,
        }

    # -----------------------------------------------------------------
    # STOCK PAPER-TRADE LIFECYCLE
    # -----------------------------------------------------------------
    def has_open_position_for_ticker(self, ticker: str) -> bool:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM stock_trades WHERE ticker = {self.ph} AND status = 'OPEN'", (ticker,))
        n = cur.fetchone()[0] or 0
        conn.close()
        return n > 0

    def open_stock_trade(self, ticker, strategy, entry, stop, target, qty, risk_amount, position_value):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO stock_trades
                (ticker, strategy, entry_price, stop_loss, target, qty, risk_amount, position_value, status, entry_time)
                VALUES ({self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph},'OPEN',CURRENT_TIMESTAMP)""",
            (ticker, strategy, entry, stop, target, qty, risk_amount, position_value),
        )
        conn.commit()
        conn.close()
        log.info(f"[STOCK OPEN] {strategy} {ticker} qty={qty} entry={entry} sl={stop} tgt={target}")

    def close_stock_trade(self, trade_id, exit_price, exit_reason, pnl):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE stock_trades SET status='CLOSED', exit_time=CURRENT_TIMESTAMP,
                exit_price={self.ph}, exit_reason={self.ph}, pnl={self.ph} WHERE id={self.ph}""",
            (exit_price, exit_reason, pnl, trade_id),
        )
        conn.commit()
        conn.close()
        self._apply_pnl(pnl)
        log.info(f"[STOCK CLOSE] id={trade_id} reason={exit_reason} exit={exit_price} pnl={pnl:.2f} balance={self.balance:.2f}")

    def get_last_price(self, symbol: str):
        """Best-effort *current* price — used only as a fallback when no
        intraday candle history is available (see get_candles_since).

        Uses yf.Ticker(...).history() — an isolated, single-symbol request
        — rather than the module-level yf.download() helper. yf.download()
        shares internal caching/threading state across calls, and this
        method is called once per open position, back-to-back, on every
        worker run. Under that rapid-fire pattern yf.download() has been
        observed to hand back a *different* ticker's candle data under the
        requested symbol's own columns, which was then accepted at face
        value — closing trades at a price that belonged to another stock
        entirely (e.g. a ~150 rupee stock being "closed" at ~4500 because
        another open ticker's price leaked in). Ticker.history() issues an
        independent request per symbol and avoids that shared state.
        """
        ticker = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
        try:
            hist = yf.Ticker(ticker).history(period="1d", interval="5m")
            closes = hist["Close"].dropna() if not hist.empty else pd.Series(dtype=float)
            if not closes.empty:
                return float(closes.iloc[-1])
        except Exception:
            pass
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d")
            closes = hist["Close"].dropna() if not hist.empty else pd.Series(dtype=float)
            if not closes.empty:
                return float(closes.iloc[-1])
        except Exception as e:
            log.warning(f"Could not fetch last price for {symbol}: {e}")
        return None

    def get_candles_since(self, ticker: str, since_dt) -> pd.DataFrame:
        """5-minute OHLC candles for `ticker` strictly after `since_dt`.

        This is what lets the square-off check catch a stop/target that was
        touched and reverted *between* two worker runs: instead of only
        looking at the latest price, it re-scans every candle since the
        trade was opened, every single run — so a delayed or skipped run
        doesn't cause a missed fill, as long as the candle is still inside
        Yahoo's ~60-day 5-minute retention window.
        """
        t = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
        try:
            # yf.Ticker(...).history() instead of yf.download() — see the
            # cross-contamination note in get_last_price() for why.
            df = yf.Ticker(t).history(period="30d", interval="5m")
            if df.empty:
                return pd.DataFrame()

            since_ts = pd.to_datetime(since_dt)
            since_ts = since_ts.tz_localize("UTC") if since_ts.tzinfo is None else since_ts.tz_convert("UTC")
            idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
            df = df.set_axis(idx)
            df = df[df.index > since_ts]
            return df[["Open", "High", "Low", "Close"]].dropna()
        except Exception as e:
            log.warning(f"Could not fetch candle history for {ticker}: {e}")
            return pd.DataFrame()

    @staticmethod
    def _first_breach(candles: pd.DataFrame, stop: float, target: float):
        """Scans candles oldest-to-newest; returns ('SL HIT', stop) or
        ('TARGET HIT', target) for the first candle whose High/Low range
        crosses either level, filled at the level itself (not the candle
        extreme). If one candle's range crosses both — a big whipsaw bar —
        SL is assumed to have happened first, per Kotegawa's capital-first
        discipline: when in doubt about fill order, assume the loss."""
        for _, row in candles.sort_index().iterrows():
            if row["Low"] <= stop:
                return "SL HIT", stop
            if row["High"] >= target:
                return "TARGET HIT", target
        return None

    def check_and_close_stock_positions(self):
        """Auto square-off: for every OPEN equity paper trade, replays every
        5-min candle since entry (not just the latest one) looking for the
        first stop-loss or target crossing, so a touch-and-reverse move
        between worker runs still gets captured correctly."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, ticker, entry_price, stop_loss, target, qty, entry_time FROM stock_trades WHERE status = 'OPEN'")
        open_rows = cur.fetchall()
        conn.close()

        for trade_id, ticker, entry, stop, target, qty, entry_time in open_rows:
            time.sleep(0.3)  # brief pacing between per-symbol requests
            candles = self.get_candles_since(ticker, entry_time) if entry_time else pd.DataFrame()

            if not candles.empty:
                breach = self._first_breach(candles, stop, target)
                if breach:
                    reason, exit_price = breach
                    pnl = round((exit_price - entry) * qty, 2)
                    self.close_stock_trade(trade_id, exit_price, reason, pnl)
                continue  # covered by candle history either way — nothing missed

            # Fallback only: no intraday candle history available at all
            # (e.g. trade is older than the ~60-day 5m retention window) —
            # check the latest price as a single point-in-time snapshot.
            price = self.get_last_price(ticker)
            if price is None:
                continue
            if price <= stop:
                pnl = round((price - entry) * qty, 2)
                self.close_stock_trade(trade_id, round(price, 2), "SL HIT", pnl)
            elif price >= target:
                pnl = round((price - entry) * qty, 2)
                self.close_stock_trade(trade_id, round(price, 2), "TARGET HIT", pnl)
            # else: still running, leave OPEN

    # -----------------------------------------------------------------
    # OPTIONS PAPER-TRADE LIFECYCLE
    # -----------------------------------------------------------------
    def has_open_position_for_index(self, index_symbol: str) -> bool:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM options_trades WHERE index_symbol = {self.ph} AND status = 'OPEN'", (index_symbol,))
        n = cur.fetchone()[0] or 0
        conn.close()
        return n > 0

    def open_options_trade(self, signal: dict):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO options_trades
                (contract_name, index_symbol, option_type, strike, expiry, lot_size, lots,
                 entry_premium, stop_loss_premium, target_premium, risk_amount, position_value, status, entry_time)
                VALUES ({self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph},{self.ph},
                         {self.ph},{self.ph},{self.ph},{self.ph},{self.ph},'OPEN',CURRENT_TIMESTAMP)""",
            (
                signal["Contract Symbol"], signal["Index"], signal["Direction"], signal["Strike"], signal["Expiry"],
                signal["Lot Size"], signal["Recommended Lots"], signal["Premium (LTP)"], signal["Stop Loss"],
                signal["Target"], signal["Risk Amount"], signal["Total Capital Needed"],
            ),
        )
        conn.commit()
        conn.close()
        log.info(f"[OPTIONS OPEN] {signal['Contract Symbol']} lots={signal['Recommended Lots']}")

    def close_options_trade(self, trade_id, exit_premium, exit_reason, pnl):
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE options_trades SET status='CLOSED', exit_time=CURRENT_TIMESTAMP,
                exit_premium={self.ph}, exit_reason={self.ph}, pnl={self.ph} WHERE id={self.ph}""",
            (exit_premium, exit_reason, pnl, trade_id),
        )
        conn.commit()
        conn.close()
        self._apply_pnl(pnl)
        log.info(f"[OPTIONS CLOSE] id={trade_id} reason={exit_reason} exit={exit_premium} pnl={pnl:.2f} balance={self.balance:.2f}")

    def get_current_option_premium(self, index_symbol, strike, expiry, option_type):
        if index_symbol == "NIFTY":
            try:
                chain = self.nse_opt.fetch_chain("NIFTY")
                contract = get_contract(chain, strike, expiry, option_type)
                return contract["ltp"] if contract else None
            except Exception as e:
                log.warning(f"Could not fetch live NIFTY premium: {e}")
                return None
        else:
            # SENSEX has no live option-chain source wired up here (same
            # limitation as at entry) — approximate premium off the current
            # spot using the same ratio used when the trade was opened.
            try:
                df = yf.download(INDEX_YF_TICKERS["SENSEX"], period="1d", interval="5m", progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                closes = df["Close"].dropna()
                if closes.empty:
                    return None
                spot_now = float(closes.iloc[-1])
                return round(spot_now * 0.005, 2)
            except Exception as e:
                log.warning(f"Could not approximate SENSEX premium: {e}")
                return None

    def check_and_close_options_positions(self):
        """Auto square-off for options: closes any OPEN paper position whose
        current premium has hit its stop-loss or target.

        Unlike stocks, this can only check the *current* premium snapshot —
        NSE's public option-chain endpoint has no historical intraday
        premium series to replay, so a touch-and-reverse in premium between
        worker runs can still be missed here. If that turns out to matter
        for your use case, the fix would be to store premium snapshots to
        the DB on every run and treat that as your own candle history going
        forward — flag it if you want that built."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, index_symbol, option_type, strike, expiry, lot_size, lots, "
            "entry_premium, stop_loss_premium, target_premium FROM options_trades WHERE status = 'OPEN'"
        )
        open_rows = cur.fetchall()
        conn.close()

        for (trade_id, index_symbol, option_type, strike, expiry, lot_size, lots,
             entry_premium, sl_premium, tgt_premium) in open_rows:
            current = self.get_current_option_premium(index_symbol, strike, expiry, option_type)
            if current is None:
                continue
            if current <= sl_premium:
                pnl = round((current - entry_premium) * lot_size * lots, 2)
                self.close_options_trade(trade_id, current, "SL HIT", pnl)
            elif current >= tgt_premium:
                pnl = round((current - entry_premium) * lot_size * lots, 2)
                self.close_options_trade(trade_id, current, "TARGET HIT", pnl)
            # else: still running, leave OPEN

    # -----------------------------------------------------------------
    # TRADE LOG (for the dashboard)
    # -----------------------------------------------------------------
    def get_trade_log(self, limit=100) -> pd.DataFrame:
        conn = self._get_connection()
        try:
            stocks = pd.read_sql_query(
                f"SELECT 'STOCK' AS asset_type, ticker AS symbol, strategy, entry_price AS entry, "
                f"stop_loss AS stop, target, qty, status, entry_time, exit_time, exit_price, exit_reason, pnl "
                f"FROM stock_trades ORDER BY entry_time DESC LIMIT {int(limit)}", conn,
            )
            options = pd.read_sql_query(
                f"SELECT 'OPTION' AS asset_type, contract_name AS symbol, option_type AS strategy, "
                f"entry_premium AS entry, stop_loss_premium AS stop, target_premium AS target, lots AS qty, "
                f"status, entry_time, exit_time, exit_premium AS exit_price, exit_reason, pnl "
                f"FROM options_trades ORDER BY entry_time DESC LIMIT {int(limit)}", conn,
            )
        finally:
            conn.close()

        combined = pd.concat([stocks, options], ignore_index=True)
        if not combined.empty:
            combined["entry_time"] = pd.to_datetime(combined["entry_time"])
            combined = combined.sort_values("entry_time", ascending=False).head(limit).reset_index(drop=True)
        return combined

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

    def scan_all_strategies(self, tickers=None, top_n=5, paper_trade=False):
        """
        paper_trade=False (default, used by the Streamlit UI): pure read-only
        scan — computes Qty for display but never writes to the DB, so
        clicking the button repeatedly can't spam trades.
        paper_trade=True (used by worker.py's scheduled run): actually opens
        a paper position for each qualifying setup, subject to the risk gate,
        the per-run open-position budget, and one-open-trade-per-ticker dedup.
        """
        if tickers is None:
            tickers = self.fetch_nse_universe("NIFTY 500")

        status = self.risk_status()
        open_slots = max(0, self.risk.max_open_positions - status["open_positions"]) if paper_trade else None
        if not status["new_entries_allowed"]:
            reason = ("daily loss circuit breaker tripped" if status["circuit_breaker_tripped"]
                       else "max open positions reached")
            log.warning(f"Risk gate closed ({reason}) — scan will run but sized quantities will be 0.")

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

                    # SWING FILTER — stop tightened to 1.5x ATR (Kotegawa: cut
                    # losses fast) with target held at >=1.5:1 reward:risk.
                    if (curr_ema20 > curr_ema50) and (curr_close > curr_ema200) and (53 <= curr_rsi <= 72) and (vol_mult >= 1.2) and (curr_close >= prev_high20 * 0.99):
                        sl = curr_close - (1.5 * curr_atr)
                        tgt = curr_close + (2.5 * curr_atr)
                        if self.risk.passes_reward_risk(curr_close, sl, tgt):
                            pos = self.risk.position_size(curr_close, sl)
                            qty = pos.qty if status["new_entries_allowed"] else 0
                            traded = self._maybe_open_stock(
                                paper_trade, "SWING", clean_symbol, curr_close, sl, tgt, qty,
                                pos.risk_amount, pos.position_value, status, open_slots,
                            )
                            if traded and open_slots is not None:
                                open_slots -= 1
                            swing_list.append({
                                "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                                "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2),
                                "Qty": qty, "Risk_Amt": pos.risk_amount, "Position_Val": pos.position_value,
                                **({"Traded": traded} if paper_trade else {}),
                            })

                    # INTRADAY FILTER — tightest stop (1.0x ATR): intraday
                    # setups get cut fastest since there's no overnight room.
                    if (curr_close > curr_ema20) and (curr_rsi >= 56) and (vol_mult >= 1.5):
                        sl = curr_close - (1.0 * curr_atr)
                        tgt = curr_close + (1.8 * curr_atr)
                        if self.risk.passes_reward_risk(curr_close, sl, tgt):
                            pos = self.risk.position_size(curr_close, sl)
                            qty = pos.qty if status["new_entries_allowed"] else 0
                            traded = self._maybe_open_stock(
                                paper_trade, "INTRADAY", clean_symbol, curr_close, sl, tgt, qty,
                                pos.risk_amount, pos.position_value, status, open_slots,
                            )
                            if traded and open_slots is not None:
                                open_slots -= 1
                            intraday_list.append({
                                "Ticker": clean_symbol, "Price": round(curr_close, 2), "RSI": round(curr_rsi, 1),
                                "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2),
                                "Qty": qty, "Risk_Amt": pos.risk_amount, "Position_Val": pos.position_value,
                                **({"Traded": traded} if paper_trade else {}),
                            })

                    # BTST FILTER
                    day_range = curr_high - curr_low
                    close_loc = (curr_close - curr_low) / day_range if day_range > 0 else 0
                    if (close_loc >= 0.80) and (58 <= curr_rsi <= 75) and (vol_mult >= 1.5) and (curr_close > prev_close):
                        sl = curr_close - (1.3 * curr_atr)
                        tgt = curr_close + (2.0 * curr_atr)
                        if self.risk.passes_reward_risk(curr_close, sl, tgt):
                            pos = self.risk.position_size(curr_close, sl)
                            qty = pos.qty if status["new_entries_allowed"] else 0
                            traded = self._maybe_open_stock(
                                paper_trade, "BTST", clean_symbol, curr_close, sl, tgt, qty,
                                pos.risk_amount, pos.position_value, status, open_slots,
                            )
                            if traded and open_slots is not None:
                                open_slots -= 1
                            btst_list.append({
                                "Ticker": clean_symbol, "Price": round(curr_close, 2), "Close_High_%": round(close_loc * 100, 1),
                                "RSI": round(curr_rsi, 1), "Vol_Mult": vol_mult, "StopLoss": round(sl, 2), "Target": round(tgt, 2),
                                "Qty": qty, "Risk_Amt": pos.risk_amount, "Position_Val": pos.position_value,
                                **({"Traded": traded} if paper_trade else {}),
                            })

                except Exception:
                    continue

        return {
            "SWING": pd.DataFrame(swing_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if swing_list else pd.DataFrame(),
            "INTRADAY": pd.DataFrame(intraday_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if intraday_list else pd.DataFrame(),
            "BTST": pd.DataFrame(btst_list).sort_values(by="Vol_Mult", ascending=False).head(top_n).reset_index(drop=True) if btst_list else pd.DataFrame()
        }

    def _maybe_open_stock(self, paper_trade, strategy, ticker, entry, sl, tgt, qty,
                           risk_amount, position_value, status, open_slots):
        """Opens a paper stock trade if paper_trade is on, entries are
        allowed, there's budget left in this run, qty is non-zero, and the
        ticker doesn't already have an open position (no stacking)."""
        if not paper_trade or not status["new_entries_allowed"] or qty <= 0:
            return False
        if open_slots is not None and open_slots <= 0:
            return False
        if self.has_open_position_for_ticker(ticker):
            return False
        self.open_stock_trade(ticker, strategy, entry, sl, tgt, qty, risk_amount, position_value)
        return True

    def evaluate_index_options(self, index_symbol="NIFTY", paper_trade=False):
        if index_symbol not in VALID_INDEX_SYMBOLS:
            return None

        status = self.risk_status()

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

        if not self.risk.passes_reward_risk(entry_premium, sl_premium, tgt_premium):
            log.info(f"[OPTIONS] {index_symbol} setup rejected: reward:risk below minimum.")
            return None

        pos = self.risk.position_size(entry_premium, sl_premium, lot_size=lot_size)

        if not status["new_entries_allowed"]:
            reason = ("daily loss circuit breaker tripped" if status["circuit_breaker_tripped"]
                       else "max open positions reached")
            lots = 0
        elif pos.lots == 0:
            reason = "risk budget too small for one lot at current premium"
            lots = 0
        else:
            reason = None
            lots = pos.lots

        signal = {
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
            "Recommended Lots": lots,
            "Risk Amount": round(lots * lot_size * (entry_premium - sl_premium), 2) if lots else 0.0,
            "Total Capital Needed": round(entry_premium * lot_size * lots, 2) if lots else 0.0,
            "Blocked Reason": reason,
        }

        if paper_trade and lots > 0 and not self.has_open_position_for_index(index_symbol):
            self.open_options_trade(signal)
            signal["Traded"] = True
        elif paper_trade:
            signal["Traded"] = False

        return signal

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

        # Stop tightened to 1.5x ATR (fast cut) with target held at a 1.67:1
        # reward:risk so the payoff structure still justifies the tight stop.
        sl = curr_close - (1.5 * curr_atr)
        tgt = curr_close + (2.5 * curr_atr)
        pos = self.risk.position_size(curr_close, sl)
        status = self.risk_status()
        qty = pos.qty if status["new_entries_allowed"] else 0

        return {
            "Symbol": ticker_symbol.replace(".NS", ""),
            "Price": round(curr_close, 2),
            "Score": f"{score}/4",
            "RSI": round(curr_rsi, 1),
            "ATR": round(curr_atr, 2),
            "EMA200": round(curr_ema200, 2),
            "StopLoss": round(sl, 2),
            "Target": round(tgt, 2),
            "Qty": qty,
            "RiskAmount": pos.risk_amount,
            "PositionValue": pos.position_value,
            "Checklist": checklist
        }

    def run(self):
        """Called by worker.py every 5 minutes during market hours. This is
        the actual paper-trading loop:
          1. Square off anything already open that has hit its SL/target.
          2. Re-check the risk gate now that balance/open-count may have changed.
          3. Scan for new setups and open paper positions for the qualifying ones.
        """
        log.info("Starting background automated execution...")

        self.check_and_close_stock_positions()
        self.check_and_close_options_positions()

        status = self.risk_status()
        log.info(
            f"[RISK] capital=₹{status['capital']} risk/trade={status['risk_per_trade_pct']}% "
            f"(₹{status['risk_per_trade_amount']}) daily_pnl=₹{status['daily_realized_pnl']} "
            f"open={status['open_positions']}/{status['max_open_positions']} "
            f"breaker_tripped={status['circuit_breaker_tripped']}"
        )
        if not status["new_entries_allowed"]:
            log.warning("[RISK] New entries are BLOCKED this run — no new paper trades will be opened.")

        universe = self.fetch_nse_universe("NIFTY 500")
        stock_results = self.scan_all_strategies(universe, top_n=5, paper_trade=True)
        for category, df in stock_results.items():
            if not df.empty:
                opened = int(df["Traded"].sum()) if "Traded" in df.columns else 0
                log.info(f"[{category}] Detected {len(df)} setups, opened {opened} paper trade(s): {df['Ticker'].tolist()}")
            else:
                log.info(f"[{category}] No active setups.")

        for idx in self.options_indices:
            try:
                sig = self.evaluate_index_options(idx, paper_trade=True)
                if sig:
                    log.info(f"[OPTIONS] Signal for {idx}: {sig['Contract Symbol']} (lots={sig['Recommended Lots']}, traded={sig.get('Traded')})")
                else:
                    log.info(f"[OPTIONS] No active setup for {idx}")
            except Exception as e:
                log.error(f"[OPTIONS] Failed evaluating {idx}: {e}")

        log.info(f"Background execution finished. Capital now ₹{self.balance:.2f}")
