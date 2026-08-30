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
