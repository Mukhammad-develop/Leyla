from datetime import datetime
from app.database.db import get_db

def get_or_create_user(telegram_id: int):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_user_id = ?", (telegram_id,)).fetchone()
        if user:
            return dict(user), False
        
        profile_id = f"telegram_{telegram_id}"
        conn.execute(
            "INSERT INTO users (telegram_user_id, hermes_profile, language, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, profile_id, "", datetime.utcnow())
        )
        conn.commit()
        
        user = conn.execute("SELECT * FROM users WHERE telegram_user_id = ?", (telegram_id,)).fetchone()
        return dict(user), True

def update_user_language(telegram_id: int, language: str):
    with get_db() as conn:
        conn.execute("UPDATE users SET language = ? WHERE telegram_user_id = ?", (language, telegram_id))
        conn.commit()

def update_last_seen(telegram_id: int):
    with get_db() as conn:
        conn.execute("UPDATE users SET last_seen_at = ? WHERE telegram_user_id = ?", (datetime.utcnow(), telegram_id))
        conn.commit()

def get_user(telegram_id: int):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_user_id = ?", (telegram_id,)).fetchone()
        return dict(user) if user else None
