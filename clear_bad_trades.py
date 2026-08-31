"""
One-time cleanup for the ticker-cross-contamination bug (see engine.py fix).

What it does
------------
1. Deletes every CLOSED row from stock_trades and options_trades — those
   entry/exit pairs were priced using the buggy fallback and can't be
   trusted (some are fine, most aren't — safest to wipe all of them).
2. Leaves OPEN rows untouched; they'll be re-evaluated by the fixed
   get_last_price()/get_candles_since() on the next worker run.
3. Resets account_state.capital back to RESET_TO_CAPITAL below, since the
   wrong pnl from the bad closes was added into your running balance via
   _apply_pnl(). Set it to whatever your capital actually was before the
   bug started corrupting closes (defaults to the engine's default
   starting capital, 100000).

Usage
-----
Run this once, from the same environment/machine that has access to your
DB (same DATABASE_URL env var if you're on Postgres, or the same working
directory as trading_paper.db if you're on SQLite):

    python clear_bad_trades.py

Then redeploy engine.py with the fix and let the worker resume.
"""

import os
import sqlite3

RESET_TO_CAPITAL = float(os.getenv("RESET_TO_CAPITAL", "100000"))


def get_connection():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        import psycopg2
        return psycopg2.connect(dsn), "%s"
    return sqlite3.connect("trading_paper.db"), "?"


def main():
    conn, ph = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM stock_trades WHERE status = 'CLOSED'")
    n_stock = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM options_trades WHERE status = 'CLOSED'")
    n_opt = cur.fetchone()[0]

    print(f"Found {n_stock} closed stock trades and {n_opt} closed options trades to remove.")
    confirm = input("Type YES to delete these rows and reset capital: ").strip()
    if confirm != "YES":
        print("Aborted — no changes made.")
        return

    cur.execute("DELETE FROM stock_trades WHERE status = 'CLOSED'")
    cur.execute("DELETE FROM options_trades WHERE status = 'CLOSED'")
    cur.execute(f"UPDATE account_state SET capital = {ph}, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (RESET_TO_CAPITAL,))
    conn.commit()
    conn.close()

    print(f"Done. Removed {n_stock + n_opt} bad closed trades and reset capital to {RESET_TO_CAPITAL:,.0f}.")


if __name__ == "__main__":
    main()
