# NSE Paper Trader Cloud V6
Render Web Service = mobile dashboard.
Render Free Postgres = persistent database for the 15-day experiment.
GitHub Actions = scheduled scanner every 5 minutes on weekdays during NSE market hours.

Render free Postgres currently expires after 30 days, which is sufficient for the planned 15-day test. Free web services can sleep when idle; that does not stop GitHub Actions from updating the database.

Render web start command:
`streamlit run app.py --server.port $PORT --server.address 0.0.0.0`

Add `DATABASE_URL` to the Render web service and as a GitHub repository secret with the same Render Postgres external URL.

GitHub scheduled workflows can be delayed, so this is not an exchange-grade real-time feed. NSE public endpoints may rate-limit or change. No real orders are placed.

## Risk Management (Kotegawa Rules)

`risk_manager.py` implements position sizing and account-level circuit
breakers modeled on the discipline attributed to Takashi Kotegawa ("BNF"),
the Japanese retail trader known for risking a small, fixed % of capital per
trade and cutting losers fast:

| Rule | Default | Env var |
|---|---|---|
| Risk per trade (% of capital) | 1.0% | `RISK_PER_TRADE_PCT` |
| Max capital in one position | 20% | `MAX_POSITION_PCT` |
| Daily loss circuit breaker | 3% of capital | `MAX_DAILY_LOSS_PCT` |
| Max simultaneous open positions | 6 | `MAX_OPEN_POSITIONS` |
| Minimum reward:risk to take a setup | 1.5 : 1 | `MIN_REWARD_RISK` |

How it's wired into `engine.py`:
- **Quantity/lots** for every Swing, Intraday, BTST, single-stock, and
  index-options signal are computed from the stop distance and the 1% risk
  budget, then clamped so no single position can exceed the 20% capital cap
  or the account's actual capital.
- **Stops are tighter than before** (e.g. Swing 1.5×ATR vs the old 2×ATR,
  Intraday 1.0×ATR) so losers are cut faster — the classic Kotegawa trait —
  while targets are set to keep every setup at least 1.5:1 reward:risk.
- **Circuit breaker**: once today's realized losses (from the `options_trades`
  table) hit the daily loss limit, `Qty`/`Recommended Lots` on new signals
  drop to 0 and the Streamlit sidebar shows a blocked state, until the next
  day's reset.
- **Max open positions**: once the number of `status='OPEN'` rows hits the
  cap, new entries are likewise sized to 0 to avoid stacking correlated risk.

The Streamlit sidebar ("🛡️ Risk Management") shows live capital, risk/trade,
max position size, open positions, today's realized PnL, and whether the
circuit breaker is tripped.

Tune the env vars above (e.g. raise `RISK_PER_TRADE_PCT` to 2% or increase
`OPTIONS_CAPITAL`) if the default 1% risk budget is too small to size even
one options lot at current premiums and lot sizes — the engine will show
`Recommended Lots: 0` with a `Blocked Reason` rather than silently
under-sizing past the risk budget.

## Paper Trading Lifecycle (auto entry + auto square-off)

The dashboard (`app.py`) is **read-only** — clicking its buttons never opens
or closes a position, so repeated clicks can't spam trades. All actual paper
trading happens in `PaperEngine.run()`, called every 5 minutes by
`worker.py` via the `scanner.yml` GitHub Actions schedule (or by `main.py`
if you run it as a long-lived loop instead). Each run does three things, in
order:

1. **Square off first.** `check_and_close_stock_positions()` and
   `check_and_close_options_positions()` fetch the latest price for every
   `OPEN` row in `stock_trades` / `options_trades` and close it — with a
   real `exit_price`, `exit_reason` (`SL HIT` / `TARGET HIT`), and `pnl` —
   the moment price has crossed the stored stop-loss or target. This is
   what actually squares off a position; nothing is "simulated" after the
   fact.
2. **Re-check the risk gate.** Capital, today's realized PnL, and open-
   position count are re-read after step 1, since a square-off can trip
   (or clear) the circuit breaker within the same run.
3. **Scan and open new trades.** `scan_all_strategies(..., paper_trade=True)`
   and `evaluate_index_options(..., paper_trade=True)` open a new `OPEN` row
   for every qualifying setup, sized by `RiskManager`, as long as: the risk
   gate is open, there's still room under `MAX_OPEN_POSITIONS` for this run,
   and there isn't already an open position on that ticker/index (no
   stacking).

**Capital persists across runs** in a single-row `account_state` table —
important because GitHub Actions starts a brand-new Python process every 5
minutes, so an in-memory balance would otherwise reset instead of
compounding with real paper P&L. Set `RESET_CAPITAL_ON_START=true` to force
it back to `OPTIONS_CAPITAL` on the next run (e.g. to restart the 15-day
experiment cleanly).

**Known limitations of the auto square-off:**
- **Stocks**: fixed to replay every 5-minute candle since entry on each run
  (not just the latest close), so a stop/target that was touched and then
  reverted between two worker runs is still correctly caught and filled at
  the stop/target level — as long as the trade is still inside Yahoo's
  ~60-day 5-minute retention window (falls back to a latest-price snapshot
  check beyond that).
- **Options**: NSE's public option-chain endpoint only exposes the current
  premium, with no historical intraday premium series to replay — so unlike
  stocks, a touch-and-reverse in premium between worker runs can still be
  missed for NIFTY/SENSEX option legs. Fixing this would require storing a
  premium snapshot on every run and building your own candle history from
  it going forward.
- SENSEX has no live option-chain source wired in at all, so its premium
  (both at entry and when checking for square-off) is approximated from the
  index spot, not a real quoted premium. NIFTY premiums use the live NSE
  option chain.
- This still places **no real orders** — it's bookkeeping in Postgres/SQLite
  only, per the project's original design.

The new **"📒 Paper Trading Account"** tab in the dashboard shows current
capital, open positions, closed trade history, win rate, and total realized
PnL — all read from the same tables the worker writes to.
