"""
SQLite database manager.

DB_PATH is resolved lazily (at first use) so that load_dotenv() has
already run before we read the environment variable.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

_db_path: str | None = None
_init_lock = threading.Lock()


def _get_db_path() -> str:
    global _db_path
    if _db_path is None:
        _db_path = os.environ.get("DATABASE_PATH", "data/app.db")
    return _db_path


def init_db() -> None:
    """Create the database file and users table if they don't exist."""
    with _init_lock:
        path = _get_db_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    hermes_profile   TEXT UNIQUE NOT NULL,
                    language         TEXT NOT NULL DEFAULT '',
                    timezone         TEXT NOT NULL DEFAULT '',
                    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                    last_seen_at     TEXT,
                    status           TEXT NOT NULL DEFAULT 'active'
                )
            """)
            # Migration check: ensure timezone column exists on existing DBs
            cursor = conn.execute("PRAGMA table_info(users)")
            cols = [row[1] for row in cursor.fetchall()]
            if "timezone" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT ''")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    chat_id          INTEGER NOT NULL,
                    remind_at        INTEGER NOT NULL,
                    text             TEXT NOT NULL,
                    language         TEXT NOT NULL DEFAULT 'en',
                    status           TEXT NOT NULL DEFAULT 'pending',
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reminders_pending 
                ON reminders (status, remind_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    name             TEXT NOT NULL,
                    phone_number     TEXT NOT NULL,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS photo_vault (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    label            TEXT NOT NULL,
                    file_path        TEXT NOT NULL,
                    file_type        TEXT NOT NULL DEFAULT 'photo',
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # Migration: add translator columns if they don't exist
            cursor = conn.execute("PRAGMA table_info(users)")
            cols = [row[1] for row in cursor.fetchall()]
            if "translator_lang" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN translator_lang TEXT NOT NULL DEFAULT ''")
            conn.commit()



@contextmanager
def get_db():
    """Yield a short-lived SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(_get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()
