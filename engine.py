import io
import os
import time
import math
import threading
import logging
import warnings
from datetime import datetime, date, timedelta, time as dt_time
from zoneinfo import ZoneInfo
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
# MARKET HOURS GUARD — NSE cash/index market: Mon–Fri, 09:15–15:30 IST.
# run() (the worker.py entry point) checks this before doing anything, so
# a cron/Action that fires outside market hours — or a manual trigger
# during off-hours — no-ops instead of opening/closing paper positions
# against stale or thin after-hours data. This does NOT account for NSE
# holidays; add a holiday-date check below if that matters for you.
# -------------------------------------------------------------------
ENGINE_VERSION = "2026.10.08-scanstats"  # bump on every release; app.py refuses to run an older engine
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_TIME = dt_time(9, 15)
MARKET_CLOSE_TIME = dt_time(15, 30)

# NSE trading holidays that fall on weekdays. Fill this in from NSE's yearly
# holiday circular (format: date(2026, 1, 26)). Can also be supplied at
# runtime without a code change: NSE_HOLIDAYS="2026-10-02,2026-11-08".
def _load_holidays_from_env() -> set:
    out = set()
    for tok in os.getenv("NSE_HOLIDAYS", "").split(","):
        tok = tok.strip()
        if tok:
            try:
                out.add(date.fromisoformat(tok))
            except ValueError:
                pass
    return out

NSE_HOLIDAYS: set = _load_holidays_from_env()

def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NSE_HOLIDAYS

def is_market_open(now: datetime | None = None) -> bool:
    now = now.astimezone(IST) if now else datetime.now(IST)
    if not is_trading_day(now.date()):  # weekends + listed NSE holidays
        return False
    return MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME

def is_near_market_close(now: datetime | None = None, buffer_minutes: int = 15) -> bool:
    """True during the last `buffer_minutes` of the trading session (e.g.
    15:15–15:30 IST). Used to force-square-off INTRADAY positions before
    close — with the worker running every 5 minutes, this window gives 2–3
    runs a chance to catch it even if one run is delayed."""
    now = now.astimezone(IST) if now else datetime.now(IST)
    if not is_trading_day(now.date()):
        return False
    close_dt = datetime.combine(now.date(), MARKET_CLOSE_TIME, tzinfo=IST)
    return (close_dt - timedelta(minutes=buffer_minutes)) <= now <= close_dt

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

def nearest_expiry_date(target_weekday: int, now: datetime | None = None) -> str:
    """Upcoming expiry date string (0=Mon, 1=Tue, 3=Thu).

    Includes TODAY when today is expiry day and the market hasn't closed yet
    (the old version always jumped a full week ahead, so expiry-day trades
    were impossible). If the expiry falls on a market holiday it rolls back to
    the previous trading day, which is how NSE/BSE handle it."""
    now = (now.astimezone(IST) if now else datetime.now(IST))
    d = now.date()
    days_ahead = (target_weekday - d.weekday()) % 7
    if days_ahead == 0 and now.time() >= MARKET_CLOSE_TIME:
        days_ahead = 7
    exp = d + timedelta(days=days_ahead)
    while not is_trading_day(exp):
        exp -= timedelta(days=1)
    return exp.strftime("%d-%b-%Y").upper()

def years_to_expiry(expiry_str: str, now: datetime | None = None) -> float:
    """Fractional years from `now` to 15:30 IST on expiry day. The old code
    floored time-to-expiry at one full day, which badly overpriced premiums on
    expiry day (a 0DTE option was priced as if it had 24h left)."""
    now = (now.astimezone(IST) if now else datetime.now(IST))
    try:
        exp = datetime.strptime(expiry_str.title(), "%d-%b-%Y").date()
    except Exception:
        return 3 / 365
    exp_dt = datetime.combine(exp, MARKET_CLOSE_TIME, tzinfo=IST)
    return max((exp_dt - now).total_seconds(), 300) / (365 * 24 * 3600)

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

def _expiry_ddmmyyyy(expiry_ddmonyyyy: str) -> str:
    """Converts our internal expiry format ('02-SEP-2025', from
    nearest_expiry_date()) to the 'DD-MM-YYYY' format the Paytm Money SDK's
    get_option_chain() takes."""
    try:
        return datetime.strptime(expiry_ddmonyyyy.title(), "%d-%b-%Y").strftime("%d-%m-%Y")
    except Exception:
        return expiry_ddmonyyyy

# -------------------------------------------------------------------
# PAYTM MONEY CLIENT — best-effort real premium source, tried before the
# NSE-chain / Black-Scholes fallback chain below.
#
# IMPORTANT OPERATIONAL CAVEAT: Paytm Money's access_token is obtained via a
# MANUAL browser login (username/password/OTP/passcode — there is no
# machine-to-machine credential flow), and per Paytm Money's own docs it
# "remains valid until midnight of the same day." There is no refresh-token.
# That means, unlike everything else in this file, this client cannot run
# unattended: PAYTM_ACCESS_TOKEN has to be regenerated and re-set as an env
# var by a human, before market open, every single trading day. On any day
# it isn't refreshed, every call below fails/returns None and the code
# transparently falls back to the existing NSE-chain / Black-Scholes path —
# it never breaks the app, it just quietly stops getting real Paytm data.
#
# SCHEMA CAVEAT: Paytm Money's option-chain response schema is not published
# anywhere I could verify (their docs site is JS-rendered and not fetchable
# here). _extract_ltp_for_strike() below parses defensively across a few
# plausible field-name/shape variants and logs the raw response the first
# time it can't find a match, so the exact shape can be nailed down from a
# real log line instead of guessed further.
# -------------------------------------------------------------------
class PaytmMoneyClient:
    def __init__(self):
        self.enabled = False
        self.client = None

        api_key = os.getenv("PAYTM_API_KEY")
        api_secret = os.getenv("PAYTM_API_SECRET")
        access_token = os.getenv("PAYTM_ACCESS_TOKEN")

        if not (api_key and api_secret):
            log.info("[PAYTM] PAYTM_API_KEY/PAYTM_API_SECRET not set — Paytm Money data source disabled.")
            return

        PMClient = None
        try:
            from pmClient.pmClient import PMClient  # matches the SDK's actual internal module path
        except ImportError:
            try:
                from pyPMClient import PMClient  # matches the SDK README's documented import
            except ImportError as e:
                log.warning(
                    f"[PAYTM] pyPMClient SDK not importable ({e}). Add "
                    f"'pyPMClient @ git+https://github.com/paytmmoney/pyPMClient.git' to requirements.txt. "
                    f"Falling back to NSE-chain/model-estimate premiums."
                )
                return

        try:
            self.client = PMClient(api_key=api_key, api_secret=api_secret)
            if access_token:
                self.client.set_access_token(access_token)
                self.enabled = True
            else:
                log.warning(
                    "[PAYTM] PAYTM_ACCESS_TOKEN not set. Call login_url() to get the login link, "
                    "complete the browser login (username/password/OTP/passcode), exchange the "
                    "resulting request_token for an access_token, and set PAYTM_ACCESS_TOKEN. It "
                    "expires at midnight and must be refreshed daily. Falling back to NSE-chain/"
                    "model-estimate premiums until then."
                )
        except Exception as e:
            log.warning(f"[PAYTM] Could not initialize client: {e}")

    def login_url(self, state_key: str = "sammy-engine") -> str | None:
        """Returns the browser login URL. Open it, log in manually
        (username/password/OTP/passcode), and Paytm Money will redirect to
        your app's configured Redirect URL with a `request_token` query
        parameter — exchange that for an access_token via generate_session()."""
        if not self.client:
            return None
        try:
            return self.client.login(state_key)
        except Exception as e:
            log.warning(f"[PAYTM] Could not build login URL: {e}")
            return None

    def generate_session(self, request_token: str) -> str | None:
        """Exchanges a one-time request_token (from the login redirect) for
        an access_token. Returns the access_token string, or None on
        failure. Set the returned value as PAYTM_ACCESS_TOKEN."""
        if not self.client:
            return None
        try:
            resp = self.client.generate_session(request_token=request_token)
            token = None
            if isinstance(resp, dict):
                data = resp.get("data", resp)
                token = data.get("access_token") if isinstance(data, dict) else None
            if token:
                self.client.set_access_token(token)
                self.enabled = True
                return token
            log.warning(f"[PAYTM] generate_session response didn't contain access_token: {str(resp)[:500]}")
        except Exception as e:
            log.warning(f"[PAYTM] generate_session failed: {e}")
        return None

    def get_option_ltp(self, symbol: str, expiry_ddmonyyyy: str, strike: float, option_type: str) -> float | None:
        """symbol: 'NIFTY' or 'SENSEX'. expiry_ddmonyyyy: our internal
        'DD-MON-YYYY' format (converted internally). option_type: 'CE'/'PE'."""
        if not self.enabled:
            return None
        try:
            resp = self.client.get_option_chain(
                type=option_type, symbol=symbol, expiry=_expiry_ddmmyyyy(expiry_ddmonyyyy)
            )
        except Exception as e:
            log.warning(f"[PAYTM] get_option_chain failed for {symbol} {strike}{option_type} {expiry_ddmonyyyy}: {e}")
            return None

        ltp = self._extract_ltp_for_strike(resp, strike)
        if ltp is None:
            log.warning(
                f"[PAYTM] Could not locate strike {strike} in get_option_chain response for "
                f"{symbol} {expiry_ddmonyyyy} {option_type} — raw response (first 500 chars): {str(resp)[:500]}"
            )
        return ltp

    @staticmethod
    def _extract_ltp_for_strike(resp, strike: float) -> float | None:
        if not resp:
            return None
        rows = resp.get("data") if isinstance(resp, dict) else resp
        if isinstance(rows, dict):
            rows = rows.get("optionChain") or rows.get("option_chain") or list(rows.values())
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_strike = row.get("strike_price", row.get("strikePrice", row.get("strike")))
            if row_strike is None:
                continue
            try:
                if float(row_strike) != float(strike):
                    continue
            except (TypeError, ValueError):
                continue
            ltp = row.get("ltp", row.get("last_price", row.get("lastPrice")))
            if ltp is not None:
                try:
                    return float(ltp)
                except (TypeError, ValueError):
                    continue
        return None

def black_scholes_premium(spot: float, strike: float, t_years: float, vol: float,
                           option_type: str, r: float = 0.065) -> float:
    """Rough Black-Scholes estimate, used only as a fallback when a live
    option-chain LTP isn't available (NSE's option-chain API blocks most
    cloud/datacenter IPs — Render, AWS, etc. — with a 401/403, which is
    the common case when this whole app runs on a hosted service rather
    than your own machine).

    This uses *realized* volatility (from recent price history) as a
    stand-in for *implied* volatility, so it will typically price a touch
    below real market premiums (real IV usually carries a risk premium
    over realized vol) — but it correctly accounts for how far the strike
    is from spot and how much time is left to expiry, which a flat
    "spot * fixed %" guess never did. Still an approximation, not a live
    quote — treat premiums computed this way as directional, not exact.
    """
    t_years = max(t_years, 1 / (365 * 24 * 12))  # 5-minute floor (was 1 full day)
    vol = max(vol, 0.05)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol ** 2) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if option_type == "CE":
        price = spot * N(d1) - strike * math.exp(-r * t_years) * N(d2)
    else:
        price = strike * math.exp(-r * t_years) * N(-d2) - spot * N(-d1)
    return round(max(price, 0.05), 2)

# -------------------------------------------------------------------
# INDEX OPTIONS — SIGNAL, EXPIRY-AWARE PARAMETERS, EXIT RULES
# -------------------------------------------------------------------
def signal_diagnostics(df: pd.DataFrame, now_ist: datetime, breakout_n: int = 8,
                       adx_min: float = 20.0, recent_candles: int = 2) -> dict:
    """Evaluates every CE and PE entry condition on COMPLETED 5-minute candles
    and returns the full checklist (so the UI can show WHY there is no call),
    plus the resulting direction when all conditions of one side pass.

    Conditions per side (CE shown; PE is the mirror image):
      1. 5-min trend:   price > EMA20 > EMA50
      2. Strength:      ADX >= adx_min (skips chop)
      3. Momentum:      RSI 50-80   (PE: 20-50)
      4. Breakout:      close above the prior `breakout_n`-candle high within
                        the last `recent_candles` candles (so a call isn't missed
                        just because you checked a few minutes after the break)
      5. 15-min trend:  not clearly AGAINST the trade (soft filter)
      6. Not stretched: price within 3 ATR of the 20 EMA
      7. Timing:        after 09:30 IST (the first 15 minutes are noise)"""
    out = {"status": None, "direction": None, "CE": [], "PE": [], "values": {}}
    if df is None or df.empty:
        out["status"] = "No 5-minute data came back from Yahoo Finance — try again in a minute."
        return out
    df = df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    if df.index[-1] + timedelta(minutes=5) > now_ist:
        df = df.iloc[:-1]  # drop the still-forming candle
    if len(df) < 60:
        out["status"] = f"Only {len(df)} five-minute candles available (60 needed for the indicators)."
        return out

    c, h, l = df["Close"], df["High"], df["Low"]
    ema_f = c.ewm(span=20, min_periods=20).mean()
    ema_s = c.ewm(span=50, min_periods=50).mean()
    rsi = ta.momentum.rsi(c, window=14)
    adx = ta.trend.adx(h, l, c, window=14)
    atr = ta.volatility.average_true_range(h, l, c, window=14)
    hh = h.rolling(breakout_n).max().shift(1)
    ll = l.rolling(breakout_n).min().shift(1)

    # 15m trend (last 15m bar may be incomplete, so drop it)
    c15 = c.resample("15min").last().dropna().iloc[:-1]
    e15f = c15.ewm(span=20, min_periods=20).mean()
    e15s = c15.ewm(span=50, min_periods=50).mean()
    if len(c15) < 50 or pd.isna(e15s.iloc[-1]):
        htf_up = htf_dn = False
        htf_txt = "15-min trend not available (not enough data) — not blocking"
    else:
        htf_up = bool(c15.iloc[-1] > e15f.iloc[-1] > e15s.iloc[-1])
        htf_dn = bool(c15.iloc[-1] < e15f.iloc[-1] < e15s.iloc[-1])
        htf_txt = "15-min trend: " + ("UP" if htf_up else ("DOWN" if htf_dn else "mixed"))

    px, ef, es = float(c.iloc[-1]), float(ema_f.iloc[-1]), float(ema_s.iloc[-1])
    r, a, at = float(rsi.iloc[-1]), float(adx.iloc[-1]), float(atr.iloc[-1])
    if any(pd.isna(x) for x in (px, ef, es, r, a, at)):
        out["status"] = "Indicators are still warming up (not enough clean candles)."
        return out

    stretch = abs(px - ef) / at if at > 0 else 0.0
    brk_up = bool((c.iloc[-recent_candles:] > hh.iloc[-recent_candles:]).any())
    brk_dn = bool((c.iloc[-recent_candles:] < ll.iloc[-recent_candles:]).any())
    hi_lvl, lo_lvl = float(hh.iloc[-1]), float(ll.iloc[-1])
    time_ok = now_ist.time() >= dt_time(9, 30)

    out["values"] = {"price": px, "ema20": ef, "ema50": es, "rsi": r, "adx": a, "atr": at,
                     "last_candle": str(df.index[-1].strftime("%H:%M")), "htf": htf_txt}
    out["CE"] = [
        ("5-min trend up: price > EMA20 > EMA50", px > ef > es, f"price {px:,.0f} | EMA20 {ef:,.0f} | EMA50 {es:,.0f}"),
        (f"Trend strength: ADX at least {adx_min:.0f}", a >= adx_min, f"ADX {a:.1f}"),
        ("Momentum: RSI between 50 and 80", 50 < r < 80, f"RSI {r:.1f}"),
        (f"Breakout above the prior {breakout_n}-candle high (last {recent_candles} candles)", brk_up, f"breakout level {hi_lvl:,.0f}"),
        ("15-min trend not against the trade", not htf_dn, htf_txt),
        ("Not over-extended (within 3 ATR of EMA20)", stretch <= 3.0, f"{stretch:.1f} ATR from EMA20"),
        ("After 09:30 IST", time_ok, now_ist.strftime("%H:%M")),
    ]
    out["PE"] = [
        ("5-min trend down: price < EMA20 < EMA50", px < ef < es, f"price {px:,.0f} | EMA20 {ef:,.0f} | EMA50 {es:,.0f}"),
        (f"Trend strength: ADX at least {adx_min:.0f}", a >= adx_min, f"ADX {a:.1f}"),
        ("Momentum: RSI between 20 and 50", 20 < r < 50, f"RSI {r:.1f}"),
        (f"Breakdown below the prior {breakout_n}-candle low (last {recent_candles} candles)", brk_dn, f"breakdown level {lo_lvl:,.0f}"),
        ("15-min trend not against the trade", not htf_up, htf_txt),
        ("Not over-extended (within 3 ATR of EMA20)", stretch <= 3.0, f"{stretch:.1f} ATR from EMA20"),
        ("After 09:30 IST", time_ok, now_ist.strftime("%H:%M")),
    ]
    if all(p for _, p, _ in out["CE"]):
        out["direction"] = "CE"
    elif all(p for _, p, _ in out["PE"]):
        out["direction"] = "PE"
    return out


def signal_v2(df: pd.DataFrame, now_ist: datetime, breakout_n: int = 8,
              adx_min: float = 20.0, recent_candles: int = 2) -> dict | None:
    """CE/PE trigger on 5m index candles — see signal_diagnostics() for the
    conditions. Returns None (no trade) or a dict with the direction/readings."""
    diag = signal_diagnostics(df, now_ist, breakout_n=breakout_n, adx_min=adx_min, recent_candles=recent_candles)
    if not diag["direction"]:
        return None
    v = diag["values"]
    return {"direction": diag["direction"], "price": v["price"], "adx": round(v["adx"], 1),
            "rsi": round(v["rsi"], 1), "atr": round(v["atr"], 2)}


def option_params(now_ist: datetime, expiry_str: str) -> dict:
    """Expiry day (0DTE) behaves differently: premium decays fast, gamma is
    high, spreads are wide. So: earlier entry cutoff, half size, wider % stop
    (premiums are small and noisy), a higher ADX bar, and skip lottery-ticket
    premiums. Starting points — backtest before trusting them."""
    try:
        is_expiry_day = datetime.strptime(expiry_str.title(), "%d-%b-%Y").date() == now_ist.date()
    except Exception:
        is_expiry_day = False
    if is_expiry_day:
        return dict(expiry_day=True, sl_pct=0.30, tgt_pct=0.50, risk_scale=0.5,
                    last_entry=dt_time(13, 30), adx_min=24.0, min_premium=15.0)
    return dict(expiry_day=False,
                sl_pct=float(os.getenv("OPTIONS_STOP_LOSS_PCT", 0.25)),
                tgt_pct=float(os.getenv("OPTIONS_TARGET_PCT", 0.40)),
                risk_scale=1.0, last_entry=dt_time(14, 45), adx_min=20.0, min_premium=10.0)


def options_exit_rules(now_ist: datetime, entry_prem: float, current_prem: float,
                       trail_stop: float | None, entry_time,
                       max_hold_minutes: int = 90):
    """Snapshot-based exit rules for an open options position.
    Returns (new_trail_stop, exit_reason_or_None).
      - trailing stop: at +20% premium gain lock breakeven, at +30% lock +10%
        (ratchets up only; a hit closes the trade as 'TRAIL SL')
      - time stop: no real progress (<10% gain) after max_hold_minutes
      - EOD square-off in the last 15 minutes (the original never closed
        options, so they carried overnight)"""
    new_trail = trail_stop
    gain = current_prem / entry_prem - 1 if entry_prem else 0.0
    if gain >= 0.30:
        new_trail = max(new_trail or 0.0, round(entry_prem * 1.10, 2))
    elif gain >= 0.20:
        new_trail = max(new_trail or 0.0, round(entry_prem, 2))

    if trail_stop is not None and current_prem <= trail_stop:
        return new_trail, "TRAIL SL"
    if is_near_market_close(now_ist):
        return new_trail, "EOD SQUARE-OFF"
    if entry_time is not None:
        et = pd.Timestamp(entry_time)
        et = et.tz_localize("UTC") if et.tzinfo is None else et
        held_min = (pd.Timestamp(now_ist) - et).total_seconds() / 60
        if held_min >= max_hold_minutes and gain < 0.10:
            return new_trail, "TIME STOP"
    return new_trail, None


# -------------------------------------------------------------------
# INDEX (NIFTY / SENSEX / BANKNIFTY) ANALYZER — used by the Stock Analyzer tab
# -------------------------------------------------------------------
INDEX_ALIASES = {
    "NIFTY": "NIFTY", "NIFTY50": "NIFTY", "^NSEI": "NIFTY",
    "SENSEX": "SENSEX", "^BSESN": "SENSEX",
    "BANKNIFTY": "BANKNIFTY", "^NSEBANK": "BANKNIFTY",
}
ANALYZER_INDEX_TICKERS = {"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}

# ---- 15-year daily history cache (index analyzer) ---------------------------
# The long history barely changes intraday, so it's cached process-wide for an
# hour (shared across browser sessions). The live 5-minute data is NOT cached,
# and today's daily bar is rebuilt from it on every call (merge_live_bar).
_DAILY_CACHE: dict = {}
_DAILY_CACHE_LOCK = threading.Lock()
DAILY_CACHE_TTL_SECONDS = int(os.getenv("DAILY_HISTORY_CACHE_SECONDS", 3600))

def get_daily_history(yf_symbol: str, period: str = "15y") -> pd.DataFrame:
    """Daily candles, cached for DAILY_CACHE_TTL_SECONDS. If a refresh fails
    (Yahoo rate-limits cloud hosts now and then) a stale cached copy is used
    instead of failing outright."""
    now = time.time()
    with _DAILY_CACHE_LOCK:
        hit = _DAILY_CACHE.get(yf_symbol)
    if hit and (now - hit[1]) < DAILY_CACHE_TTL_SECONDS:
        return hit[0].copy()
    try:
        df = yf.Ticker(yf_symbol).history(period=period, interval="1d")
        if df is not None and not df.empty:
            with _DAILY_CACHE_LOCK:
                _DAILY_CACHE[yf_symbol] = (df, now)
            return df.copy()
    except Exception as e:
        log.warning(f"Daily history refresh failed for {yf_symbol}: {e}")
    if hit:
        log.warning(f"Using STALE cached daily history for {yf_symbol} ({int((now - hit[1]) / 60)} min old).")
        return hit[0].copy()
    return pd.DataFrame()

def daily_cache_age_minutes(yf_symbol: str) -> float | None:
    with _DAILY_CACHE_LOCK:
        hit = _DAILY_CACHE.get(yf_symbol)
    return round((time.time() - hit[1]) / 60, 1) if hit else None

def merge_live_bar(d: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """Overwrite (or append) the latest daily bar from the live 5-minute
    candles, so cached history never makes today's price/High/Low stale."""
    if d.empty or m.empty:
        return d
    session = m.index[-1].date()
    today = m[m.index.date == session]
    if today.empty:
        return d
    row = {"Open": float(today["Open"].iloc[0]), "High": float(today["High"].max()),
           "Low": float(today["Low"].min()), "Close": float(today["Close"].iloc[-1]), "Volume": 0}
    d = d.copy()
    last_date = d.index[-1].date()
    if last_date > session:
        return d
    if last_date == session:
        for k, v in row.items():
            if k in d.columns:
                d.loc[d.index[-1], k] = v
        return d
    ts = pd.Timestamp(session)
    if d.index.tz is not None:
        ts = ts.tz_localize(d.index.tz)
    new = pd.DataFrame([row], index=[ts]).reindex(columns=d.columns).fillna(0.0)
    return pd.concat([d, new])


def historical_edge(d: pd.DataFrame, name: str, horizons=(1, 3, 5),
                    min_cases: int = 30, min_episodes: int = 15) -> dict | None:
    """Base rates from the index's own history. Answers: "on days that looked
    like today (same daily bias bucket, and the same RSI zone), what happened
    over the next 1 / 3 / 5 trading days — and how does that compare with ALL
    days?" The all-days baseline is the honest yardstick: most indices rise on
    ~52-55% of days regardless, so a 55% "up" rate after some pattern is no edge.

    Safeguards against fooling yourself:
      - forward returns look only forward (shift(-h)); no look-ahead
      - overlapping cases are clustered, so we report separate EPISODES too
      - a percentage is only trusted with >= min_cases and >= min_episodes
      - the pattern must lean the same way in the earlier AND the recent half
        of history, otherwise it's flagged as unstable
    Uses daily factors only (the intraday factors have no long history)."""
    d = d.dropna(subset=["Close"])
    if len(d) < 400:
        return None
    close = d["Close"].astype(float)
    ema20 = ta.trend.ema_indicator(close, window=20)
    ema50 = ta.trend.ema_indicator(close, window=50)
    ema200 = ta.trend.ema_indicator(close, window=200)
    rsi = ta.momentum.rsi(close, window=14)
    ret = close.pct_change() * 100

    raw = (np.where(close > ema200, 1, -1) + np.where(ema20 > ema50, 1, -1)
           + np.where(close > ema20, 1, -1)
           + np.where(rsi > 55, 1, np.where(rsi < 45, -1, 0))
           + np.where(ret > 0.15, 1, np.where(ret < -0.15, -1, 0)))
    valid = (ema200.notna() & rsi.notna() & ret.notna()).to_numpy()
    score = pd.Series(raw, index=close.index).where(valid)
    bucket = pd.Series(np.where(score >= 3, "UP", np.where(score <= -3, "DOWN", "MIXED")),
                       index=close.index).where(valid)
    zone = pd.cut(rsi, [-np.inf, 30, 45, 55, 70, np.inf],
                  labels=["oversold (<30)", "weak (30-45)", "neutral (45-55)",
                          "strong (55-70)", "overbought (>70)"]).astype(object)
    zone = pd.Series(zone, index=close.index)

    if pd.isna(score.iloc[-1]) or pd.isna(zone.iloc[-1]):
        return None
    b_now, z_now = str(bucket.iloc[-1]), str(zone.iloc[-1])
    vmask = pd.Series(valid, index=close.index)
    split = close.index[vmask.to_numpy()][int(vmask.sum() // 2)]

    def stats(fwd, m, baseline=False):
        m = m & fwd.notna()
        x = fwd[m]
        n = int(len(x))
        if n == 0:
            return {"n": 0, "episodes": 0, "ok": False}
        pos = np.flatnonzero(m.to_numpy())
        episodes = 1 + int((np.diff(pos) > 5).sum())
        first, second = x[x.index < split], x[x.index >= split]
        return {
            "n": n, "episodes": episodes,
            "ok": n >= min_cases and (baseline or episodes >= min_episodes),
            "pct_up": float((x > 0).mean() * 100), "pct_down": float((x < 0).mean() * 100),
            "avg": float(x.mean()), "median": float(x.median()),
            "p10": float(x.quantile(0.10)), "p90": float(x.quantile(0.90)),
            "up_first": float((first > 0).mean() * 100) if len(first) >= 10 else None,
            "up_second": float((second > 0).mean() * 100) if len(second) >= 10 else None,
        }

    table, by_h = [], {}
    for h in horizons:
        fwd = (close.shift(-h) / close - 1) * 100
        sets = [
            ("All days (baseline)", vmask),
            (f"Same bias ({b_now})", vmask & (bucket == b_now)),
            (f"Bias {b_now} + RSI {z_now}", vmask & (bucket == b_now) & (zone == z_now)),
        ]
        by_h[h] = [(label, stats(fwd, m, baseline=(i == 0))) for i, (label, m) in enumerate(sets)]
        for label, st_ in by_h[h]:
            if st_["n"] == 0:
                continue
            reliable = st_["ok"]
            table.append({
                "Horizon": f"{h}d", "Situation": label, "Cases": st_["n"], "Episodes": (st_["episodes"] if label != "All days (baseline)" else None),
                "% Up": round(st_["pct_up"], 1) if reliable else None,
                "% Down": round(st_["pct_down"], 1) if reliable else None,
                "Avg move %": round(st_["avg"], 2) if reliable else None,
                "Median %": round(st_["median"], 2) if reliable else None,
                "10th pct %": round(st_["p10"], 2) if reliable else None,
                "90th pct %": round(st_["p90"], 2) if reliable else None,
                "Reliable": "yes" if reliable else "too few cases",
            })

    # ---- plain-English verdict on the 3-day horizon
    base_label, base = by_h[3][0]
    verdict = None
    for label, st_ in (by_h[3][2], by_h[3][1]):        # narrowest reliable set first
        if not st_["ok"]:
            continue
        diff = st_["pct_up"] - base["pct_up"]
        halves = [st_["up_first"], st_["up_second"]]
        p0 = min(max(base["pct_up"] / 100, 0.05), 0.95)
        z = abs(diff / 100) / ((p0 * (1 - p0) / max(st_["episodes"], 1)) ** 0.5)
        significant = z >= 2.0
        stable = (None not in halves) and all((hv - base["pct_up"]) * diff > 0 and abs(hv - base["pct_up"]) >= 2 for hv in halves)
        head = (f"In {st_['n']} past days matching \"{label}\" ({st_['episodes']} separate episodes), "
                f"{name} was higher 3 trading days later {st_['pct_up']:.0f}% of the time "
                f"(lower {st_['pct_down']:.0f}%), versus {base['pct_up']:.0f}% up across all days. "
                f"Typical outcomes ranged {st_['p10']:+.1f}% to {st_['p90']:+.1f}%.")
        if abs(diff) >= 5 and significant and stable:
            verdict = (head + f" That's a {abs(diff):.0f}-point lean toward {'UP' if diff > 0 else 'DOWN'} moves, "
                       "and it held in both the earlier and recent halves of history. A tendency, not a forecast.")
            strength = "lean"
        elif abs(diff) >= 5:
            verdict = (head + f" There is a {abs(diff):.0f}-point difference, but it is not statistically solid "
                       "and/or did not hold in both halves of history, so treat it as unreliable.")
            strength = "unstable"
        else:
            verdict = head + " That is within normal noise of the baseline — no reliable edge from this pattern."
            strength = "none"
        break
    if verdict is None:
        verdict = ("Too few similar past cases for a reliable percentage. Treat the bias as a description "
                   "of current conditions only.")
        strength = "insufficient"

    return {
        "state": {"daily_score": int(score.iloc[-1]), "bucket": b_now, "rsi_zone": z_now},
        "table": table, "verdict": verdict, "strength": strength,
        "years": round(len(d) / 252, 1), "since": str(close.index[0].date()),
    }


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
    ("trail_stop_premium", "FLOAT", "REAL"),  # ratcheting trailing stop (see options_exit_rules)
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
        self.engine_version = ENGINE_VERSION
        self.last_scan_stats = None  # filled by scan_all_strategies()
        self.risk_per_trade_pct = risk_per_trade_pct
        env_initial_balance = float(os.getenv("OPTIONS_CAPITAL", initial_balance))

        self.db_dsn = os.getenv("DATABASE_URL")
        self.ph = "%s" if self.db_dsn else "?"  # SQL parameter placeholder style
        self.ensure_schema()
        self._maybe_clear_closed_trades()

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
        self.paytm = PaytmMoneyClient()
        self._vol_cache = {}  # {yf_symbol: (annualized_vol, cached_at_epoch_seconds)}

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
    def _maybe_clear_closed_trades(self):
        """One-time cleanup for the ticker-cross-contamination / off-hours
        bug: deletes every CLOSED row from both trade tables so the wrong
        entry/exit pairs stop showing up on the dashboard. Runs only when
        CLEAR_CLOSED_TRADES_ON_START=true is set, so a normal restart never
        touches trade history.

        IMPORTANT: this runs on every process start while the env var is
        set — including every redeploy. Set it to true, deploy once, watch
        the logs for the "[CLEANUP]" line confirming it ran, then remove
        the variable (or set it back to false) and redeploy again so it
        doesn't keep wiping future closed trades.
        """
        if os.getenv("CLEAR_CLOSED_TRADES_ON_START", "false").lower() != "true":
            return
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM stock_trades WHERE status = 'CLOSED'")
            n_stock = cur.rowcount
            cur.execute("DELETE FROM options_trades WHERE status = 'CLOSED'")
            n_opt = cur.rowcount
            conn.commit()
            conn.close()
            log.warning(
                f"[CLEANUP] CLEAR_CLOSED_TRADES_ON_START=true \u2014 deleted "
                f"{n_stock} closed stock trades and {n_opt} closed options trades. "
                f"Remove this env var now so future restarts don't keep wiping history."
            )
        except Exception as e:
            log.warning(f"[CLEANUP] Could not clear closed trades: {e}")

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

    def _fetch_candles_since(self, yf_ticker: str, since_dt) -> pd.DataFrame:
        """Core candle-fetch used by both get_candles_since() (stocks) and
        get_index_candles_since() (options' underlying index) — takes the
        already-correct Yahoo ticker as-is, no suffix handling."""
        try:
            df = yf.Ticker(yf_ticker).history(period="30d", interval="5m")
            if df.empty:
                return pd.DataFrame()

            since_ts = pd.to_datetime(since_dt)
            since_ts = since_ts.tz_localize("UTC") if since_ts.tzinfo is None else since_ts.tz_convert("UTC")
            idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
            df = df.set_axis(idx)
            df = df[df.index > since_ts]
            return df[["Open", "High", "Low", "Close"]].dropna()
        except Exception as e:
            log.warning(f"Could not fetch candle history for {yf_ticker}: {e}")
            return pd.DataFrame()

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
        return self._fetch_candles_since(t, since_dt)

    def get_index_candles_since(self, yf_symbol: str, since_dt) -> pd.DataFrame:
        """Same idea as get_candles_since(), but for a raw index ticker
        (e.g. "^NSEI") with no ".NS" suffix handling — used to replay the
        underlying's candles for options square-off (see
        _first_option_breach for why we replay the *underlying's* candles
        rather than the option's own)."""
        return self._fetch_candles_since(yf_symbol, since_dt)

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
        between worker runs still gets captured correctly. Additionally,
        any still-open INTRADAY position is force-closed at the current
        price during the last 15 minutes of the session — Intraday trades
        must not carry overnight."""
        if not is_market_open():
            log.info("[MARKET] Market closed — skipping stock square-off check.")
            return

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, ticker, strategy, entry_price, stop_loss, target, qty, entry_time FROM stock_trades WHERE status = 'OPEN'")
        open_rows = cur.fetchall()
        conn.close()

        near_close = is_near_market_close()

        for trade_id, ticker, strategy, entry, stop, target, qty, entry_time in open_rows:
            time.sleep(0.3)  # brief pacing between per-symbol requests
            candles = self.get_candles_since(ticker, entry_time) if entry_time else pd.DataFrame()
            closed_this_pass = False

            if not candles.empty:
                breach = self._first_breach(candles, stop, target)
                if breach:
                    reason, exit_price = breach
                    pnl = round((exit_price - entry) * qty, 2)
                    self.close_stock_trade(trade_id, exit_price, reason, pnl)
                    closed_this_pass = True

            elif candles.empty:
                # Fallback only: no intraday candle history available at all
                # (e.g. trade is older than the ~60-day 5m retention window) —
                # check the latest price as a single point-in-time snapshot.
                price = self.get_last_price(ticker)
                if price is not None:
                    if price <= stop:
                        pnl = round((price - entry) * qty, 2)
                        self.close_stock_trade(trade_id, round(price, 2), "SL HIT", pnl)
                        closed_this_pass = True
                    elif price >= target:
                        pnl = round((price - entry) * qty, 2)
                        self.close_stock_trade(trade_id, round(price, 2), "TARGET HIT", pnl)
                        closed_this_pass = True

            # No overnight carry for INTRADAY: if neither SL nor target has
            # been hit and the session is about to close, force it flat at
            # the current price instead of leaving it OPEN into tomorrow.
            if not closed_this_pass and strategy == "INTRADAY" and near_close:
                price = self.get_last_price(ticker)
                if price is not None:
                    pnl = round((price - entry) * qty, 2)
                    self.close_stock_trade(trade_id, round(price, 2), "EOD SQUARE-OFF", pnl)
                else:
                    log.warning(f"[EOD] Could not fetch price to square off INTRADAY {ticker} (id={trade_id}) — will retry next run before close.")

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

    def _historical_volatility(self, yf_symbol: str) -> float:
        """Annualized realized volatility from ~3 months of daily closes.
        Feeds black_scholes_premium() when a live option-chain isn't
        reachable. Cached for an hour — this doesn't need recomputing on
        every 5-minute worker run."""
        cached = self._vol_cache.get(yf_symbol)
        if cached and (time.time() - cached[1]) < 3600:
            return cached[0]
        vol = 0.13  # sane fallback: roughly NIFTY's typical realized vol
        try:
            hist = yf.Ticker(yf_symbol).history(period="3mo", interval="1d")
            closes = hist["Close"].dropna()
            log_returns = np.log(closes / closes.shift(1)).dropna()
            computed = float(log_returns.std() * np.sqrt(252))
            if computed and computed > 0:
                vol = computed
        except Exception as e:
            log.warning(f"Could not compute historical volatility for {yf_symbol}: {e}")
        self._vol_cache[yf_symbol] = (vol, time.time())
        return vol

    def _estimate_option_premium(self, index_symbol: str, spot: float, strike: float,
                                  expiry: str, option_type: str) -> float:
        """Black-Scholes premium estimate using realized volatility — the
        fallback used whenever a live option-chain LTP isn't available.
        See black_scholes_premium()'s docstring for the accuracy caveat."""
        yf_symbol = INDEX_YF_TICKERS.get(index_symbol, "^NSEI")
        t_years = years_to_expiry(expiry)  # intraday-accurate, incl. expiry day
        vol = self._historical_volatility(yf_symbol)
        return black_scholes_premium(spot, strike, t_years, vol, option_type)

    def get_current_option_premium(self, index_symbol, strike, expiry, option_type):
        # Paytm Money first, when a valid access_token is set — real live
        # premium for both NIFTY and SENSEX. Silently skipped (falls through
        # to NSE chain / model estimate) if not configured or the token has
        # expired for the day. See PaytmMoneyClient's docstring for why this
        # can't always be relied on.
        paytm_ltp = self.paytm.get_option_ltp(index_symbol, expiry, strike, option_type)
        if paytm_ltp is not None:
            return paytm_ltp

        if index_symbol == "NIFTY":
            try:
                chain = self.nse_opt.fetch_chain("NIFTY")
                contract = get_contract(chain, strike, expiry, option_type)
                if contract and contract.get("ltp"):
                    return contract["ltp"]
            except Exception as e:
                log.warning(f"Could not fetch live NIFTY premium, falling back to model estimate: {e}")
            # Chain unreachable (or strike/expiry not found) — estimate off
            # the current spot instead of giving up. Previously this
            # returned None here, which meant an unreachable NSE chain
            # (the common case on cloud hosts) silently left every open
            # NIFTY options position stuck OPEN forever, since the
            # square-off check just skips a None reading.
            yf_symbol = INDEX_YF_TICKERS["NIFTY"]
        else:
            # SENSEX has no public NSE option-chain source — always model-estimated.
            yf_symbol = INDEX_YF_TICKERS["SENSEX"]

        try:
            hist = yf.Ticker(yf_symbol).history(period="1d", interval="5m")
            closes = hist["Close"].dropna()
            if closes.empty:
                return None
            spot_now = float(closes.iloc[-1])
            return self._estimate_option_premium(index_symbol, spot_now, strike, expiry, option_type)
        except Exception as e:
            log.warning(f"Could not estimate {index_symbol} premium: {e}")
            return None

    @staticmethod
    def _first_option_breach(index_candles: pd.DataFrame, strike: float, expiry_date, option_type: str,
                              vol: float, sl_premium: float, tgt_premium: float):
        """Mirrors _first_breach() for stocks, replaying candles
        oldest-to-newest for the first SL/target crossing — but NSE
        exposes no historical intraday *option* premium series to replay,
        only the underlying index's candles. So each candle's premium
        range is estimated via Black-Scholes off that candle's own
        High/Low spot (same realized-vol model used at entry), and we
        look for the first candle whose estimated premium range would
        have crossed either level. For a CE, spot High → premium High and
        spot Low → premium Low; for a PE it's the mirror image. Same
        "assume SL happened first on an ambiguous candle" convention as
        stocks. Filled at the level itself, not the estimated extreme —
        this is a model-based replay, not a real historical fill."""
        for ts, row in index_candles.sort_index().iterrows():
            candle_end = ts.astimezone(IST) + timedelta(minutes=5)
            exp_dt = datetime.combine(expiry_date, MARKET_CLOSE_TIME, tzinfo=IST)
            t_years = max((exp_dt - candle_end).total_seconds(), 300) / (365 * 24 * 3600)
            prem_at_spot_high = black_scholes_premium(float(row["High"]), strike, t_years, vol, option_type)
            prem_at_spot_low = black_scholes_premium(float(row["Low"]), strike, t_years, vol, option_type)
            if option_type == "CE":
                candle_prem_high, candle_prem_low = prem_at_spot_high, prem_at_spot_low
            else:
                candle_prem_high, candle_prem_low = prem_at_spot_low, prem_at_spot_high
            if candle_prem_low <= sl_premium:
                return "SL HIT", sl_premium
            if candle_prem_high >= tgt_premium:
                return "TARGET HIT", tgt_premium
        return None

    def check_and_close_options_positions(self):
        """Auto square-off for options. Two layers, both run every worker pass:
          1. Candle replay — the underlying index's 5-min candles since entry
             are replayed (premium range estimated via Black-Scholes per
             candle, see _first_option_breach) for the first SL/target
             crossing, so a touch-and-reverse between runs is still caught.
          2. Snapshot exit rules on the current premium — trailing stop
             (breakeven at +20%, lock +10% at +30%), a 90-minute time stop for
             trades that go nowhere, and a forced EOD square-off in the last 15
             minutes of the session (options never carry overnight).
        """
        if not is_market_open():
            log.info("[MARKET] Market closed — skipping options square-off check.")
            return

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, index_symbol, option_type, strike, expiry, lot_size, lots, "
            "entry_premium, stop_loss_premium, target_premium, entry_time, trail_stop_premium "
            "FROM options_trades WHERE status = 'OPEN'"
        )
        open_rows = cur.fetchall()
        conn.close()
        now_ist = datetime.now(IST)

        for (trade_id, index_symbol, option_type, strike, expiry, lot_size, lots,
             entry_premium, sl_premium, tgt_premium, entry_time, trail_stop) in open_rows:
            time.sleep(0.3)  # brief pacing between per-symbol requests
            yf_symbol = INDEX_YF_TICKERS.get(index_symbol, "^NSEI")
            candles = self.get_index_candles_since(yf_symbol, entry_time) if entry_time else pd.DataFrame()

            # ---- layer 1: candle replay for SL / target
            if not candles.empty:
                try:
                    expiry_date = datetime.strptime(expiry.title(), "%d-%b-%Y").date()
                except Exception:
                    expiry_date = date.today()
                vol = self._historical_volatility(yf_symbol)
                breach = self._first_option_breach(candles, strike, expiry_date, option_type, vol, sl_premium, tgt_premium)
                if breach:
                    reason, exit_premium = breach
                    pnl = round((exit_premium - entry_premium) * lot_size * lots, 2)
                    self.close_options_trade(trade_id, exit_premium, reason, pnl)
                    continue

            # ---- layer 2: snapshot on the current live/estimated premium
            current = self.get_current_option_premium(index_symbol, strike, expiry, option_type)
            if current is None:
                # Can't price it. Still honour the EOD square-off using the
                # last known level so nothing is left open overnight.
                if is_near_market_close(now_ist):
                    log.warning(f"[EOD] No premium available to square off options id={trade_id}; will retry next run.")
                continue

            if current <= sl_premium:
                pnl = round((current - entry_premium) * lot_size * lots, 2)
                self.close_options_trade(trade_id, current, "SL HIT", pnl)
                continue
            if current >= tgt_premium:
                pnl = round((current - entry_premium) * lot_size * lots, 2)
                self.close_options_trade(trade_id, current, "TARGET HIT", pnl)
                continue

            new_trail, exit_reason = options_exit_rules(now_ist, entry_premium, current, trail_stop, entry_time)
            if exit_reason:
                pnl = round((current - entry_premium) * lot_size * lots, 2)
                self.close_options_trade(trade_id, current, exit_reason, pnl)
            elif new_trail is not None and new_trail != trail_stop:
                self._set_trail_stop(trade_id, new_trail)
                log.info(f"[OPTIONS] id={trade_id} trailing stop raised to {new_trail}")

    def _set_trail_stop(self, trade_id, trail_stop):
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(f"UPDATE options_trades SET trail_stop_premium = {self.ph} WHERE id = {self.ph}", (trail_stop, trade_id))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Could not persist trailing stop for id={trade_id}: {e}")

    def index_entry_blocked(self, index_symbol: str, now_ist: datetime,
                            max_trades_per_day: int = 3, cooldown_minutes: int = 30) -> str | None:
        """Overtrading guard for options: at most `max_trades_per_day` entries
        per index per day, and a cooldown after a stop-loss/trail exit."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                f"SELECT entry_time, exit_time, exit_reason FROM options_trades "
                f"WHERE index_symbol = {self.ph} ORDER BY id DESC LIMIT 20", (index_symbol,))
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            log.warning(f"Could not read options history for entry guard: {e}")
            return None

        def _ist(ts):
            t = pd.Timestamp(ts)
            t = t.tz_localize("UTC") if t.tzinfo is None else t
            return t.tz_convert(IST)

        today_entries = [r for r in rows if r[0] is not None and _ist(r[0]).date() == now_ist.date()]
        if len(today_entries) >= max_trades_per_day:
            return f"max {max_trades_per_day} {index_symbol} option trades per day reached"
        for _, exit_time, reason in rows:
            if exit_time is not None and reason in ("SL HIT", "TRAIL SL"):
                mins = (pd.Timestamp(now_ist) - _ist(exit_time)).total_seconds() / 60
                if mins < cooldown_minutes:
                    return f"cooling off {int(cooldown_minutes - mins)} min after a stop-out on {index_symbol}"
                break  # only the most recent stop-out matters
        return None

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

            # Timestamps are stored in UTC (CURRENT_TIMESTAMP) but the
            # dashboard is used from India — convert to IST for display so
            # e.g. "03:48" (UTC, actually 09:18 IST — right at market open)
            # doesn't look like an off-hours trade.
            for col in ("entry_time", "exit_time"):
                ts = pd.to_datetime(combined[col], utc=True, errors="coerce")
                combined[col] = ts.dt.tz_convert(IST).dt.tz_localize(None)
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

        if paper_trade and not is_market_open():
            log.info("[MARKET] Market closed — scanning for display only, no paper trades will be opened.")
            paper_trade = False

        status = self.risk_status()
        open_slots = max(0, self.risk.max_open_positions - status["open_positions"]) if paper_trade else None
        if not status["new_entries_allowed"]:
            reason = ("daily loss circuit breaker tripped" if status["circuit_breaker_tripped"]
                       else "max open positions reached")
            log.warning(f"Risk gate closed ({reason}) — scan will run but sized quantities will be 0.")

        swing_list, intraday_list, btst_list = [], [], []
        # Diagnostics only (does not affect any filter): lets the UI tell "Yahoo
        # returned nothing" apart from "data fine, no stock qualifies".
        stats = {"total": len(tickers), "usable": 0, "liquid": 0, "above_200": 0,
                 "above_20": 0, "failed_batches": 0, "batches": 0}

        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            chunk = tickers[i:i + batch_size]
            stats["batches"] += 1
            try:
                data = yf.download(tickers=chunk, period="1y", interval="1d", group_by='ticker', threads=True, progress=False)
            except Exception:
                stats["failed_batches"] += 1
                continue

            for ticker in chunk:
                try:
                    df = data[ticker].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                    if len(df) < 50:
                        continue
                    stats["usable"] += 1

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
                    stats["liquid"] += 1
                    stats["above_200"] += int(curr_close > curr_ema200)
                    stats["above_20"] += int(curr_close > curr_ema20)

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

        stats["scanned_at"] = datetime.now(IST).strftime("%d-%b %H:%M IST")
        stats["matches"] = {"SWING": len(swing_list), "INTRADAY": len(intraday_list), "BTST": len(btst_list)}
        self.last_scan_stats = stats

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

    def explain_index_options(self, index_symbol="NIFTY") -> dict:
        """Why is there (or isn't there) a CE/PE call right now? Returns the
        full condition checklist for both sides so the dashboard can show
        exactly which filter is blocking, instead of a bare 'no setup'."""
        now_ist = datetime.now(IST)
        info = {"index": index_symbol, "time": now_ist.strftime("%a %d-%b %H:%M IST"),
                "market_open": is_market_open(now_ist), "status": None}
        if index_symbol not in VALID_INDEX_SYMBOLS:
            info["status"] = f"{index_symbol} is not a supported index."
            return info
        weekday = 1 if index_symbol == "NIFTY" else 3
        expiry = nearest_expiry_date(target_weekday=weekday, now=now_ist)
        params = option_params(now_ist, expiry)
        info.update({"expiry": expiry, "expiry_day": params["expiry_day"],
                     "entry_cutoff": params["last_entry"].strftime("%H:%M"),
                     "past_cutoff": now_ist.time() > params["last_entry"]})
        if not info["market_open"]:
            info["status"] = ("The NSE market is closed right now (Mon–Fri 09:15–15:30 IST, excluding "
                              "holidays), so there are no live calls.")
            return info
        try:
            df = yf.Ticker(INDEX_YF_TICKERS.get(index_symbol, "^NSEI")).history(period="5d", interval="5m")
        except Exception as e:
            info["status"] = f"Could not fetch 5-minute data: {e}"
            return info
        diag = signal_diagnostics(df, now_ist, adx_min=params["adx_min"])
        info.update(diag)
        if diag["status"] is None:
            ce_n = sum(1 for _, p, _ in diag["CE"] if p)
            pe_n = sum(1 for _, p, _ in diag["PE"] if p)
            side = "CE" if ce_n >= pe_n else "PE"
            info["closest"] = side
            info["closest_met"] = max(ce_n, pe_n)
            info["closest_total"] = len(diag[side])
            info["missing"] = [lbl for lbl, p, _ in diag[side] if not p]
        return info

    def evaluate_index_options(self, index_symbol="NIFTY", paper_trade=False):
        if index_symbol not in VALID_INDEX_SYMBOLS:
            return None

        if not is_market_open():
            # No live/estimated premium is shown outside market hours — the
            # model uses realized (not implied) vol and would not match the
            # real closing premium.
            log.info("[MARKET] Market closed — skipping options evaluation (no live/estimated premium to show).")
            return None

        now_ist = datetime.now(IST)
        status = self.risk_status()

        # Expiry first: on expiry day the rules (cutoff time, sizing, stop %) change.
        if index_symbol == "NIFTY":
            lot_size = self.lot_sizes["NIFTY"]
            expiry = nearest_expiry_date(target_weekday=1, now=now_ist)  # Tuesday weekly expiry
            step = 50.0
        else:
            lot_size = self.lot_sizes["SENSEX"]
            expiry = nearest_expiry_date(target_weekday=3, now=now_ist)  # Thursday weekly expiry
            step = 100.0
        params = option_params(now_ist, expiry)

        late_reason = None
        if now_ist.time() > params["last_entry"]:
            late_reason = (f"past the {params['last_entry'].strftime('%H:%M')} entry cutoff"
                           f"{' (expiry day)' if params['expiry_day'] else ''} — no new entries")
            log.info(f"[OPTIONS] {index_symbol}: {late_reason}.")

        yf_symbol = INDEX_YF_TICKERS.get(index_symbol, "^NSEI")
        df = yf.Ticker(yf_symbol).history(period="5d", interval="5m")
        if df.empty:
            return None

        sig = signal_v2(df, now_ist, adx_min=params["adx_min"])
        if not sig:
            return None
        direction = sig["direction"]
        spot = round(sig["price"], 2)
        strike = atm_strike(spot, step=step)

        # ---- entry premium: Paytm (live) -> NSE chain (NIFTY) -> Black-Scholes model
        premium_source = "Paytm Money (live)"
        entry_premium = self.paytm.get_option_ltp(index_symbol, expiry, strike, direction)
        if entry_premium is None and index_symbol == "NIFTY":
            try:
                chain = self.nse_opt.fetch_chain(index_symbol)
                spot = spot_price(chain) or spot
                contract = get_contract(chain, strike, expiry, direction)
                if contract and contract.get("ltp"):
                    entry_premium = contract["ltp"]
                    premium_source = "NSE option chain (live)"
            except Exception:
                pass
        if entry_premium is None:
            entry_premium = self._estimate_option_premium(index_symbol, spot, strike, expiry, direction)
            premium_source = "Model estimate (not a live quote)"

        if entry_premium is None:
            log.info(f"[OPTIONS] {index_symbol} setup found but no premium could be priced.")
            return None
        low_premium_reason = None
        if entry_premium < params["min_premium"]:
            low_premium_reason = (f"premium ₹{entry_premium:.2f} is below the ₹{params['min_premium']:.0f} "
                                  f"minimum (lottery-ticket zone)")
            log.info(f"[OPTIONS] {index_symbol}: {low_premium_reason}.")

        contract_name = f"{index_symbol} {int(strike)} {direction} {expiry}"
        sl_premium = round(entry_premium * (1 - params["sl_pct"]), 2)
        tgt_premium = round(entry_premium * (1 + params["tgt_pct"]), 2)

        if not self.risk.passes_reward_risk(entry_premium, sl_premium, tgt_premium):
            log.info(f"[OPTIONS] {index_symbol} setup rejected: reward:risk below minimum.")
            return None

        # Size with the expiry-day risk scale applied (temporarily), then restore.
        orig_risk_pct = self.risk.risk_per_trade_pct
        self.risk.risk_per_trade_pct = orig_risk_pct * params["risk_scale"]
        try:
            pos = self.risk.position_size(entry_premium, sl_premium, lot_size=lot_size)
        finally:
            self.risk.risk_per_trade_pct = orig_risk_pct
        sized_lots = pos.lots
        if sized_lots == 0 and params["risk_scale"] < 1:
            # Half-size can't fund even one lot on a small account: fall back to
            # a single lot, but only if that lot still fits the NORMAL 1x risk budget.
            full = self.risk.position_size(entry_premium, sl_premium, lot_size=lot_size)
            if full.lots >= 1:
                sized_lots = 1

        guard_reason = self.index_entry_blocked(index_symbol, now_ist) if paper_trade else None
        if not status["new_entries_allowed"]:
            reason = ("daily loss circuit breaker tripped" if status["circuit_breaker_tripped"]
                       else "max open positions reached")
            lots = 0
        elif late_reason:
            reason = late_reason
            lots = 0
        elif low_premium_reason:
            reason = low_premium_reason
            lots = 0
        elif guard_reason:
            reason = guard_reason
            lots = 0
        elif sized_lots == 0:
            reason = "risk budget too small for one lot at current premium"
            lots = 0
        else:
            reason = None
            lots = sized_lots

        signal = {
            "Contract Symbol": contract_name,
            "Index": index_symbol,
            "Direction": direction,
            "Spot Price": spot,
            "Strike": strike,
            "Expiry": expiry,
            "Expiry Day": params["expiry_day"],
            "Premium (LTP)": round(entry_premium, 2),
            "Stop Loss": sl_premium,
            "Target": tgt_premium,
            "Lot Size": lot_size,
            "Recommended Lots": lots,
            "Risk Amount": round(lots * lot_size * (entry_premium - sl_premium), 2) if lots else 0.0,
            "Total Capital Needed": round(entry_premium * lot_size * lots, 2) if lots else 0.0,
            "ADX": sig["adx"],
            "RSI": sig["rsi"],
            "Premium Source": premium_source,
            "Blocked Reason": reason,
        }

        if paper_trade and lots > 0 and not self.has_open_position_for_index(index_symbol):
            self.open_options_trade(signal)
            signal["Traded"] = True
        elif paper_trade:
            signal["Traded"] = False

        return signal

    def analyze_index(self, name: str) -> dict:
        """Up / down / sideways read on NIFTY, SENSEX or BANKNIFTY using the
        daily trend plus how the index is behaving in the current (or last)
        session. Volume is NOT used — Yahoo reports no volume for indices.
        Keeps every key analyze_stock() returns so the UI doesn't break."""
        yf_symbol = ANALYZER_INDEX_TICKERS[name]
        try:
            d = get_daily_history(yf_symbol)                       # 15y, cached ~1h
            m = yf.Ticker(yf_symbol).history(period="5d", interval="5m")   # always live
            d = merge_live_bar(d, m)                               # today's bar from live data
        except Exception as e:
            return {"Error": f"Could not fetch {name} data: {e}"}
        if d.empty or m.empty or len(d) < 60:
            return {"Error": f"Not enough data for {name}"}

        close, high, low = d["Close"], d["High"], d["Low"]
        ema20 = ta.trend.ema_indicator(close, window=20)
        ema50 = ta.trend.ema_indicator(close, window=50)
        ema200 = ta.trend.ema_indicator(close, window=200)
        rsi = ta.momentum.rsi(close, window=14)
        atr = ta.volatility.average_true_range(high, low, close, window=14)
        adx = ta.trend.adx(high, low, close, window=14)

        # ---- today's (or the last session's) intraday picture
        session = m.index[-1].date()
        today = m[m.index.date == session]
        prev_daily = d[d.index.date < session]
        prev_close = float(prev_daily["Close"].iloc[-1]) if not prev_daily.empty else float(today["Open"].iloc[0])
        prev_high = float(prev_daily["High"].iloc[-1]) if not prev_daily.empty else None
        prev_low = float(prev_daily["Low"].iloc[-1]) if not prev_daily.empty else None

        price = float(m["Close"].iloc[-1])
        day_high, day_low = float(today["High"].max()), float(today["Low"].min())
        day_chg = (price / prev_close - 1) * 100
        day_rng = day_high - day_low
        pos_in_range = (price - day_low) / day_rng if day_rng > 0 else 0.5

        m_ema20 = float(m["Close"].ewm(span=20, min_periods=20).mean().iloc[-1])
        m_ema50 = float(m["Close"].ewm(span=50, min_periods=50).mean().iloc[-1])
        intraday_up = price > m_ema20 > m_ema50
        intraday_dn = price < m_ema20 < m_ema50

        c_px, c20, c50, c200 = float(close.iloc[-1]), float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
        c_rsi, c_atr, c_adx = float(rsi.iloc[-1]), float(atr.iloc[-1]), float(adx.iloc[-1])

        # ---- signed factors: +1 bullish, -1 bearish, 0 neutral
        factors = [
            ("Price vs 200-day EMA (macro trend)", 1 if c_px > c200 else -1),
            ("20 EMA vs 50 EMA (daily trend)", 1 if c20 > c50 else -1),
            ("Price vs 20-day EMA", 1 if c_px > c20 else -1),
            (f"Daily RSI {c_rsi:.1f}", 1 if c_rsi > 55 else (-1 if c_rsi < 45 else 0)),
            ("5-min trend (price / 20 / 50 EMA)", 1 if intraday_up else (-1 if intraday_dn else 0)),
            (f"Session change {day_chg:+.2f}% vs previous close", 1 if day_chg > 0.15 else (-1 if day_chg < -0.15 else 0)),
            (f"Position in session range {pos_in_range * 100:.0f}%", 1 if pos_in_range > 0.65 else (-1 if pos_in_range < 0.35 else 0)),
        ]
        total = sum(s for _, s in factors)
        bias = "UP" if total >= 3 else ("DOWN" if total <= -3 else "SIDEWAYS / MIXED")
        strength = "strong trend" if c_adx >= 25 else ("moderate trend" if c_adx >= 18 else "weak / choppy")

        checklist = [f"{'✓' if s > 0 else ('✗' if s < 0 else '•')} {label}" for label, s in factors]
        checklist.append(f"• ADX {c_adx:.1f} ({strength})")

        # ---- stretch / exhaustion warnings (a low RSI counts as bearish above, but
        # deeply oversold markets bounce often, so flag it explicitly)
        warnings_list = []
        dist200 = (c_px / c200 - 1) * 100
        if c_rsi < 30:
            warnings_list.append(f"Daily RSI {c_rsi:.1f} is OVERSOLD — after sharp falls, relief bounces are common; the bearish read may be stretched.")
        elif c_rsi > 70:
            warnings_list.append(f"Daily RSI {c_rsi:.1f} is OVERBOUGHT — pullbacks are common after sharp rallies; the bullish read may be stretched.")
        if abs(dist200) >= 5:
            warnings_list.append(f"Price is {dist200:+.1f}% from its 200-day EMA — an extended move; late entries carry reversal risk.")
        bias_note = bias
        if bias == "DOWN" and c_rsi < 30:
            bias_note = "DOWN (but oversold — bounce risk)"
        elif bias == "UP" and c_rsi > 70:
            bias_note = "UP (but overbought — pullback risk)"

        try:
            history = historical_edge(d, name)
        except Exception as e:
            log.warning(f"historical_edge failed for {name}: {e}")
            history = None

        return {
            "Symbol": name,
            "Is_Index": True,
            "Price": round(price, 2),
            "Bias": bias,
            "Bias_Note": bias_note,
            "Warnings": warnings_list,
            "History": history,
            "Bias_Score": f"{total:+d} / {len(factors)}",
            "Trend_Strength": strength,
            "Day_Change_%": round(day_chg, 2),
            "Session": str(session),
            "History_Cache_Age_Min": daily_cache_age_minutes(yf_symbol),
            "Score": f"{total:+d}/{len(factors)}",
            "RSI": round(c_rsi, 1),
            "ATR": round(c_atr, 2),
            "ADX": round(c_adx, 1),
            "EMA200": round(c200, 2),
            "Levels": {
                "Session High": round(day_high, 2),
                "Session Low": round(day_low, 2),
                "Prev Day High": round(prev_high, 2) if prev_high else None,
                "Prev Day Low": round(prev_low, 2) if prev_low else None,
                "20-Day High": round(float(high.tail(20).max()), 2),
                "20-Day Low": round(float(low.tail(20).min()), 2),
                "Expected Range Low (-1 ATR)": round(price - c_atr, 2),
                "Expected Range High (+1 ATR)": round(price + c_atr, 2),
            },
            # Not applicable — an index isn't directly tradable. Keys kept for UI compatibility.
            "StopLoss": None, "Target": None, "Qty": 0, "RiskAmount": 0, "PositionValue": 0,
            "Checklist": checklist,
        }

    def analyze_stock(self, symbol):
        key = symbol.strip().upper().replace(" ", "")
        if key in INDEX_ALIASES:
            return self.analyze_index(INDEX_ALIASES[key])
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

        if not is_market_open():
            log.info(
                "[MARKET] NSE market is closed right now (outside Mon\u2013Fri "
                "09:15\u201315:30 IST) \u2014 skipping this run entirely. No positions "
                "will be opened or closed."
            )
            return

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
