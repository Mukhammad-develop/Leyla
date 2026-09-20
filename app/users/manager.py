"""
User ↔ Hermes profile mapping and metadata.
"""

from datetime import datetime, timezone
from app.database.db import get_db


def get_or_create_user(telegram_id: int) -> tuple[dict, bool]:
    """Return (user_dict, is_new).  Creates the row on first contact."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_id,),
        ).fetchone()
        if row:
            return dict(row), False

        profile_id = f"telegram_{telegram_id}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO users (telegram_user_id, hermes_profile, language, created_at) "
            "VALUES (?, ?, '', ?)",
            (telegram_id, profile_id, now),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_id,),
        ).fetchone()
        return dict(row), True


def update_user_language(telegram_id: int, language: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET language = ? WHERE telegram_user_id = ?",
            (language, telegram_id),
        )
        conn.commit()


def update_user_timezone(telegram_id: int, timezone_str: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE telegram_user_id = ?",
            (timezone_str.strip(), telegram_id),
        )
        conn.commit()


def update_last_seen(telegram_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET last_seen_at = ? WHERE telegram_user_id = ?",
            (now, telegram_id),
        )
        conn.commit()


def get_user(telegram_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_id,),
        ).fetchone()
        return dict(row) if row else None


def update_translator_mode(telegram_id: int, target_lang: str) -> None:
    """
    Enable or disable translator mode.
    Pass a non-empty language code (e.g. 'zh', 'en') to enable,
    or an empty string '' to disable.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET translator_lang = ? WHERE telegram_user_id = ?",
            (target_lang.strip(), telegram_id),
        )
        conn.commit()


def get_all_users() -> list[dict]:
    """Return all registered users from the database."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]


def get_recent_users(limit: int = 10) -> list[dict]:
    """Newest users first (for the admin panel)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_user_city(telegram_id: int, city: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET city = ? WHERE telegram_user_id = ?",
            (city.strip(), telegram_id),
        )
        conn.commit()


def update_voice_mode(telegram_id: int, mode: str) -> None:
    """Set reply mode: 'text', 'voice', or 'both'."""
    if mode not in ("text", "voice", "both"):
        return
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET voice_mode = ? WHERE telegram_user_id = ?",
            (mode, telegram_id),
        )
        conn.commit()


def update_briefing_enabled(telegram_id: int, enabled: bool) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET briefing_enabled = ? WHERE telegram_user_id = ?",
            (1 if enabled else 0, telegram_id),
        )
        conn.commit()


def update_briefing_time(telegram_id: int, time_str: str) -> None:
    """Set daily briefing time, expected 'HH:MM' (user local)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET briefing_time = ? WHERE telegram_user_id = ?",
            (time_str.strip(), telegram_id),
        )
        conn.commit()


def update_briefing_last_sent(telegram_id: int, date_str: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET briefing_last_sent = ? WHERE telegram_user_id = ?",
            (date_str, telegram_id),
        )
        conn.commit()


def update_calorie_goal(telegram_id: int, goal: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET calorie_goal = ? WHERE telegram_user_id = ?",
            (max(0, int(goal)), telegram_id),
        )
        conn.commit()
