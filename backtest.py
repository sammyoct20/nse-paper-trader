"""
Backtests the strategy in strategy.py against historical OHLCV data.

Why this design:
- No lookahead: a signal computed on bar i's CLOSE is filled at bar i+1's OPEN,
  same as a real system would (you can't trade on a close you're still watching happen).
- Exits are checked intrabar using High/Low, not just Close, since a stop or target
  can be hit and reverse within the same 5m candle.
- If both stop and target are inside the same bar's range, we assume the STOP was
  hit first (conservative - don't let the backtest flatter itself).
- Position sizing is recomputed off current equity as trades close, not a fixed
  starting number, so compounding (or drawdown shrinking size) is reflected.

USAGE
-----
Run in an environment that can actually reach Yahoo Finance (this sandbox can't -
its network egress is locked to package registries only):

    pip install yfinance pandas --break-system-packages
    python3 backtest.py                     # single run with current strategy.py defaults
    python3 backtest.py --grid              # grid-search over a small parameter space

You can also feed it your own CSV files (columns: Datetime,Open,High,Low,Close,Volume)
if you've exported historical data from your broker - see load_from_csv() below.
"""
import argparse
import itertools
import sys
from dataclasses import replace

import pandas as pd

from strategy_core import StrategyConfig, compute_indicators, evaluate_row

SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "LT.NS", "SBIN.NS", "ITC.NS", "AXISBANK.NS", "KOTAKBANK.NS",
]


# ----------------------------------
# DATA LOADING
# ----------------------------------
def load_from_yfinance(symbol, period="60d", interval="5m"):
    """
    yfinance caps intraday (5m) history at 60 days - that's a real constraint,
    not a bug. For a longer/more statistically meaningful backtest, use daily
    bars (interval='1d', period='2y') or stitch multiple 60-day pulls together.
    """
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close", "Volume"])


def load_from_csv(path):
    """Expects columns: Datetime,Open,High,Low,Close,Volume (Datetime as index)."""
    df = pd.read_csv(path, parse_dates=["Datetime"], index_col="Datetime")
    return df.dropna(subset=["Close", "Volume"])


# ----------------------------------
# BACKTEST CORE
# ----------------------------------
class Position:
    __slots__ = ("symbol", "entry", "stop_loss", "target", "qty", "entry_idx", "entry_time")

    def __init__(self, symbol, entry, stop_loss, target, qty, entry_idx, entry_time):
        self.symbol = symbol
        self.entry = entry
        self.stop_loss = stop_loss
        self.target = target
        self.qty = qty
        self.entry_idx = entry_idx
        self.entry_time = entry_time


def run_backtest(data: dict, cfg: StrategyConfig, verbose=False):
    """
    data: {symbol: DataFrame} with OHLCV, all indexed by datetime, already indicator-free.
    Returns (trades_df, equity_curve_df, summary_dict).
    """
    prepared = {sym: compute_indicators(df, cfg) for sym, df in data.items()}

    # unified timeline across all symbols so we walk forward in real chronological order
    all_times = sorted(set().union(*[set(df.index) for df in prepared.values()]))

    equity = cfg.capital
    open_positions: dict[str, Position] = {}
    closed_trades = []
    equity_curve = []

    idx_lookup = {sym: {t: i for i, t in enumerate(df.index)} for sym, df in prepared.items()}

    for t in all_times:
        # 1) check exits on open positions first (using this bar's High/Low)
        for sym in list(open_positions.keys()):
            df = prepared[sym]
            if t not in idx_lookup[sym]:
                continue
            i = idx_lookup[sym][t]
            bar = df.iloc[i]
            pos = open_positions[sym]

            hit_stop = float(bar["Low"]) <= pos.stop_loss
            hit_target = float(bar["High"]) >= pos.target

            exit_price, reason = None, None
            if hit_stop:  # conservative: stop wins ties
                exit_price, reason = pos.stop_loss, "STOP LOSS"
            elif hit_target:
                exit_price, reason = pos.target, "TARGET HIT"

            if exit_price is not None:
                pnl = (exit_price - pos.entry) * pos.qty
                equity += pnl
                closed_trades.append({
                    "symbol": sym, "entry_time": pos.entry_time, "exit_time": t,
                    "entry": pos.entry, "exit": exit_price, "stop_loss": pos.stop_loss,
                    "target": pos.target, "qty": pos.qty, "reason": reason, "pnl": pnl,
                    "bars_held": i - pos.entry_idx,
                })
                del open_positions[sym]
                if verbose:
                    print(f"{t} EXIT  {sym:12s} {reason:12s} pnl={pnl:9.2f} equity={equity:.2f}")

        # 2) look for new entries if slots are free (signal was formed on the PRIOR bar,
        #    filled at this bar's open - no lookahead)
        slots = cfg.max_trades - len(open_positions)
        if slots > 0:
            for sym, df in prepared.items():
                if slots == 0:
                    break
                if sym in open_positions:
                    continue
                if t not in idx_lookup[sym]:
                    continue
                i = idx_lookup[sym][t]
                if i < 1:
                    continue

                sig = evaluate_row(df, i - 1, cfg, capital_for_sizing=equity)
                if sig is None:
                    continue

                fill_price = float(df.iloc[i]["Open"])
                # re-derive stop/target off the actual fill price, keep qty from the signal
                risk_per_share = sig["entry"] - sig["stop_loss"]
                stop_loss = fill_price - risk_per_share
                target = fill_price + (sig["target"] - sig["entry"])
                if stop_loss <= 0 or stop_loss >= fill_price:
                    continue

                open_positions[sym] = Position(
                    symbol=sym, entry=fill_price, stop_loss=stop_loss, target=target,
                    qty=sig["qty"], entry_idx=i, entry_time=t,
                )
                slots -= 1
                if verbose:
                    print(f"{t} ENTER {sym:12s} entry={fill_price:.2f} "
                          f"sl={stop_loss:.2f} tgt={target:.2f} qty={sig['qty']}")

        equity_curve.append({"time": t, "equity": equity})

    # close anything still open at the last available bar of that symbol, at last close
    for sym, pos in open_positions.items():
        df = prepared[sym]
        last_price = float(df.iloc[-1]["Close"])
        pnl = (last_price - pos.entry) * pos.qty
        equity += pnl
        closed_trades.append({
            "symbol": sym, "entry_time": pos.entry_time, "exit_time": df.index[-1],
            "entry": pos.entry, "exit": last_price, "stop_loss": pos.stop_loss,
            "target": pos.target, "qty": pos.qty, "reason": "END OF DATA", "pnl": pnl,
            "bars_held": len(df) - 1 - pos.entry_idx,
        })

    trades_df = pd.DataFrame(closed_trades)
    equity_df = pd.DataFrame(equity_curve)
    summary = summarize(trades_df, equity_df, cfg.capital)
    return trades_df, equity_df, summary


def summarize(trades_df: pd.DataFrame, equity_df: pd.DataFrame, starting_capital: float) -> dict:
    if trades_df.empty:
        return {"trades": 0, "note": "No trades were generated - filters may be too strict "
                                      "for this data window, or the window is too short."}

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]

    gross_win = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    final_equity = starting_capital + trades_df["pnl"].sum()

    # max drawdown on the equity curve
    max_dd = 0.0
    if not equity_df.empty:
        peak = equity_df["equity"].cummax()
        dd = (equity_df["equity"] - peak) / peak
        max_dd = dd.min()

    return {
        "trades": len(trades_df),
        "win_rate_pct": round(100 * len(wins) / len(trades_df), 1),
        "avg_win": round(wins["pnl"].mean(), 2) if len(wins) else 0,
        "avg_loss": round(losses["pnl"].mean(), 2) if len(losses) else 0,
        "profit_factor": round(profit_factor, 2),
        "total_pnl": round(trades_df["pnl"].sum(), 2),
        "return_pct": round(100 * (final_equity - starting_capital) / starting_capital, 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "avg_bars_held": round(trades_df["bars_held"].mean(), 1),
        "stop_loss_exits": int((trades_df["reason"] == "STOP LOSS").sum()),
        "target_exits": int((trades_df["reason"] == "TARGET HIT").sum()),
    }


# ----------------------------------
# PARAMETER GRID SEARCH
# ----------------------------------
def grid_search(data: dict, base_cfg: StrategyConfig):
    """
    Small, sane grid over the parameters most likely to matter. Expand as needed -
    kept small here so it runs in a reasonable time.
    """
    grid = {
        "atr_stop_mult": [1.0, 1.5, 2.0],
        "atr_target_mult": [2.0, 3.0, 4.0],
        "volume_mult": [1.2, 1.5, 2.0],
        "rsi_max": [65, 70, 75],
    }
    keys = list(grid.keys())
    results = []

    for combo in itertools.product(*[grid[k] for k in keys]):
        cfg = replace(base_cfg, **dict(zip(keys, combo)))
        trades_df, equity_df, summary = run_backtest(data, cfg)
        if summary.get("trades", 0) == 0:
            continue
        row = dict(zip(keys, combo))
        row.update(summary)
        results.append(row)

    results_df = pd.DataFrame(results)
    if results_df.empty:
        return results_df
    return results_df.sort_values(["profit_factor", "return_pct"], ascending=False)


# ----------------------------------
# CLI
# ----------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", action="store_true", help="run parameter grid search instead of a single backtest")
    parser.add_argument("--period", default="60d", help="yfinance period (60d max for 5m interval)")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--csv-dir", default=None, help="optional dir of {SYMBOL}.csv files instead of yfinance")
    args = parser.parse_args()

    cfg = StrategyConfig()

    data = {}
    for sym in SYMBOLS:
        if args.csv_dir:
            path = f"{args.csv_dir}/{sym}.csv"
            try:
                df = load_from_csv(path)
            except FileNotFoundError:
                print(f"skip {sym}: {path} not found", file=sys.stderr)
                continue
        else:
            df = load_from_yfinance(sym, period=args.period, interval=args.interval)

        if df is None or len(df) < max(cfg.atr_period, cfg.ema_slow) + 5:
            print(f"skip {sym}: insufficient data", file=sys.stderr)
            continue
        data[sym] = df

    if not data:
        print("No data loaded for any symbol. Check your network/CSV paths.", file=sys.stderr)
        sys.exit(1)

    if args.grid:
        results = grid_search(data, cfg)
        if results.empty:
            print("Grid search produced no trades for any parameter combination.")
            return
        pd.set_option("display.width", 160)
        print(results.head(15).to_string(index=False))
        results.to_csv("grid_search_results.csv", index=False)
        print("\nFull results written to grid_search_results.csv")
    else:
        trades_df, equity_df, summary = run_backtest(data, cfg, verbose=True)
        print("\n==== SUMMARY ====")
        for k, v in summary.items():
            print(f"{k}: {v}")
        if not trades_df.empty:
            trades_df.to_csv("backtest_trades.csv", index=False)
            print("\nTrade log written to backtest_trades.csv")


if __name__ == "__main__":
    main()
