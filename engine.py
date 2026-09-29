"""
Drop-in upgrades for unified_engine.py (options side).

INTEGRATION
1. Paste/import these functions into unified_engine.py.
2. In evaluate_index_options(): replace the df/indicator/direction block with
   `sig = signal_v2(df, now_ist)`; use sig["direction"]. Then use
   `p = option_params(now_ist, expiry)` for SL/target %, strike and cutoffs.
3. Replace nearest_expiry_date() with nearest_expiry_date_v2().
4. Replace the days-based time-to-expiry in _estimate_option_premium() and
   _first_option_breach() with years_to_expiry() (fixes expiry-day overpricing).
5. In black_scholes_premium(), change `max(t_years, 1/365)` to
   `max(t_years, 1/(365*24*12))` (5-minute floor instead of 1 day).
6. Call options_exit_rules() inside check_and_close_options_positions().
7. Add is_trading_day() to is_market_open().
All thresholds are starting points. Log signals and backtest before trusting them.
"""
from datetime import datetime, date, timedelta, time as dt_time
from zoneinfo import ZoneInfo
import pandas as pd
import ta

IST = ZoneInfo("Asia/Kolkata")
CLOSE_T = dt_time(15, 30)

# Fill from the NSE holiday list each year (trading holidays on weekdays).
NSE_HOLIDAYS: set[date] = set()

def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


# ---------------------------------------------------------------- expiry --
def nearest_expiry_date_v2(target_weekday: int, now: datetime | None = None) -> str:
    """Includes TODAY if it is expiry day and the market is still open
    (the old version always jumped a week ahead). Rolls back over holidays,
    since NSE moves expiry to the previous trading day."""
    now = now or datetime.now(IST)
    d = now.date()
    days_ahead = (target_weekday - d.weekday()) % 7
    if days_ahead == 0 and now.time() >= CLOSE_T:
        days_ahead = 7
    exp = d + timedelta(days=days_ahead)
    while not is_trading_day(exp):
        exp -= timedelta(days=1)
    return exp.strftime("%d-%b-%Y").upper()


def years_to_expiry(expiry_str: str, now: datetime) -> float:
    """Fractional years to 15:30 IST on expiry day. Old code floored at one
    full day, which badly overprices premiums on expiry day."""
    exp = datetime.strptime(expiry_str.title(), "%d-%b-%Y").date()
    exp_dt = datetime.combine(exp, CLOSE_T, tzinfo=IST)
    now = now.astimezone(IST)
    return max((exp_dt - now).total_seconds(), 300) / (365 * 24 * 3600)


# ---------------------------------------------------------------- signal --
def signal_v2(df: pd.DataFrame, now_ist: datetime, breakout_n: int = 12,
              adx_min: float = 20.0) -> dict | None:
    """Stricter CE/PE trigger on 5m index candles. Fixes vs the original:
    - uses only COMPLETED candles (original read the still-forming one)
    - ADX filter to skip chop
    - N-candle breakout instead of just the previous candle
    - 15m trend must agree with the 5m trend
    - skips over-extended moves (price far from the 20 EMA)
    - no entries in the first 15 minutes
    """
    df = df.copy()
    if df.index[-1] + timedelta(minutes=5) > now_ist:
        df = df.iloc[:-1]                      # drop forming candle
    if len(df) < 60:
        return None
    if now_ist.time() < dt_time(9, 30):
        return None

    c, h, l = df["Close"], df["High"], df["Low"]
    ema_f = c.ewm(span=20, min_periods=20).mean()
    ema_s = c.ewm(span=50, min_periods=50).mean()
    rsi = ta.momentum.rsi(c, window=14)
    adx = ta.trend.adx(h, l, c, window=14)
    atr = ta.volatility.average_true_range(h, l, c, window=14)
    hh = h.rolling(breakout_n).max().shift(1)
    ll = l.rolling(breakout_n).min().shift(1)

    # 15m trend filter (drop the last, possibly incomplete, 15m bar)
    c15 = c.resample("15min").last().dropna().iloc[:-1]
    e15f = c15.ewm(span=20, min_periods=20).mean()
    e15s = c15.ewm(span=50, min_periods=50).mean()
    if e15s.isna().iloc[-1]:
        htf_up = htf_dn = True                 # not enough data, don't block
    else:
        htf_up = c15.iloc[-1] > e15f.iloc[-1] > e15s.iloc[-1]
        htf_dn = c15.iloc[-1] < e15f.iloc[-1] < e15s.iloc[-1]

    px, ef, es = float(c.iloc[-1]), float(ema_f.iloc[-1]), float(ema_s.iloc[-1])
    r, a, at = float(rsi.iloc[-1]), float(adx.iloc[-1]), float(atr.iloc[-1])
    if pd.isna(a) or a < adx_min:
        return None
    stretched = abs(px - ef) > 1.5 * at

    direction = None
    if px > ef > es and 52 < r < 72 and px > float(hh.iloc[-1]) and htf_up and not stretched:
        direction = "CE"
    elif px < ef < es and 28 < r < 48 and px < float(ll.iloc[-1]) and htf_dn and not stretched:
        direction = "PE"
    if not direction:
        return None
    return {"direction": direction, "price": px, "adx": round(a, 1),
            "rsi": round(r, 1), "atr": round(at, 2)}


# ------------------------------------------------- expiry-aware parameters --
def option_params(now_ist: datetime, expiry_str: str) -> dict:
    """Expiry day (0DTE) behaves differently: premium decays fast, gamma is
    high, spreads are wide. So: earlier entry cutoff, smaller size, wider %
    stop (premiums are small and noisy), higher ADX bar, skip lottery premiums."""
    is_expiry_day = datetime.strptime(expiry_str.title(), "%d-%b-%Y").date() == now_ist.date()
    if is_expiry_day:
        return dict(expiry_day=True, sl_pct=0.30, tgt_pct=0.50, risk_scale=0.5,
                    last_entry=dt_time(13, 30), adx_min=25.0, min_premium=15.0)
    return dict(expiry_day=False, sl_pct=0.25, tgt_pct=0.40, risk_scale=1.0,
                last_entry=dt_time(14, 45), adx_min=20.0, min_premium=10.0)


# ------------------------------------------------------------ exit rules --
def options_exit_rules(now_ist: datetime, entry_prem: float, sl_prem: float,
                       current_prem: float, entry_time: datetime,
                       max_hold_minutes: int = 90):
    """Returns (new_sl, exit_reason_or_None). Call per open option trade.
    - trailing: at +20% premium move SL to breakeven; at +30% lock +10%
    - time stop: no progress after max_hold_minutes -> exit
    - EOD square-off in the last 15 minutes (original never closed options)
    """
    new_sl = sl_prem
    gain = current_prem / entry_prem - 1
    if gain >= 0.30:
        new_sl = max(new_sl, round(entry_prem * 1.10, 2))
    elif gain >= 0.20:
        new_sl = max(new_sl, round(entry_prem, 2))

    if now_ist.time() >= dt_time(15, 15):
        return new_sl, "EOD SQUARE-OFF"
    et = pd.Timestamp(entry_time)
    et = et.tz_localize("UTC") if et.tzinfo is None else et
    held = (pd.Timestamp(now_ist) - et).total_seconds() / 60
    if held >= max_hold_minutes and gain < 0.10:
        return new_sl, "TIME STOP"
    return new_sl, None
