import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DATABASE_PATH", "data/app.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id INTEGER PRIMARY KEY,
                hermes_profile TEXT UNIQUE NOT NULL,
                language TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
        conn.commit()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
