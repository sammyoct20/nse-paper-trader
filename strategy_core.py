"""
Shared strategy logic for the paper-trading engine and the backtester.
Keeping this in one place means the backtest is testing the *exact* same
signal code that runs live - no drift between the two.
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class StrategyConfig:
    capital: float = 100000
    max_trades: int = 3
    risk_per_trade_pct: float = 0.01
    atr_period: int = 14
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 3.0
    volume_mult: float = 1.5
    rsi_period: int = 14
    rsi_min: float = 30
    rsi_max: float = 70
    min_price: float = 20
    ema_fast: int = 20
    ema_slow: int = 50


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(50)


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, min_periods=period).mean()


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Adds ema_fast, ema_slow, rsi, atr columns. Does not mutate the input df."""
    out = df.copy()
    out["ema_fast"] = out["Close"].ewm(span=cfg.ema_fast, min_periods=cfg.ema_fast).mean()
    out["ema_slow"] = out["Close"].ewm(span=cfg.ema_slow, min_periods=cfg.ema_slow).mean()
    out["rsi"] = rsi(out["Close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["avg_vol20"] = out["Volume"].rolling(20).mean()
    return out


def evaluate_row(df: pd.DataFrame, i: int, cfg: StrategyConfig, capital_for_sizing: float):
    """
    Evaluate the entry rule using only data up to and including row i (no lookahead).
    df must already have indicators from compute_indicators().
    Returns a signal dict (entry/stop_loss/target/qty computed off row i's Close) or None.
    capital_for_sizing lets the backtester size positions off current equity rather
    than a fixed starting capital.
    """
    if i < 1:
        return None

    latest = df.iloc[i]
    prev = df.iloc[i - 1]

    price = float(latest["Close"])
    ema_fast = float(latest["ema_fast"])
    ema_slow = float(latest["ema_slow"])
    r = float(latest["rsi"])
    a = float(latest["atr"])
    volume = float(latest["Volume"])
    avg_volume = float(latest["avg_vol20"])

    if any(pd.isna(x) for x in (ema_fast, ema_slow, r, a, avg_volume)):
        return None
    if a <= 0 or price < cfg.min_price:
        return None

    # trend filter
    if not (price > ema_fast > ema_slow):
        return None

    # momentum: confirmed but not already overbought
    if not (cfg.rsi_min < r < cfg.rsi_max):
        return None

    # volume confirmation
    if avg_volume <= 0 or volume < cfg.volume_mult * avg_volume:
        return None

    # breakout vs prior bar
    if price <= float(prev["High"]):
        return None

    stop_loss = price - cfg.atr_stop_mult * a
    target = price + cfg.atr_target_mult * a
    if stop_loss <= 0 or stop_loss >= price:
        return None

    risk_amount = capital_for_sizing * cfg.risk_per_trade_pct
    risk_per_share = price - stop_loss
    qty_by_risk = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
    qty_by_capital = int((capital_for_sizing / cfg.max_trades) / price)
    qty = max(0, min(qty_by_risk, qty_by_capital))

    if qty < 1:
        return None

    return {
        "entry": round(price, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "qty": qty,
    }
