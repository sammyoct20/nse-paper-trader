import sqlite3

DB_NAME = "trades.db"

def get_conn():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def create_tables():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        entry REAL,
        sl REAL,
        target REAL,
        status TEXT,
        entry_price REAL,
        exit_price REAL,
        pnl REAL,
        type TEXT,
        direction TEXT,
        exit_reason TEXT,
        atr REAL,
        adx REAL,
        volume_ratio REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()
