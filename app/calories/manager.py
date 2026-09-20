"""
Calorie counter — per-day food log in SQLite.
"""

import logging

from app.database.db import get_db

logger = logging.getLogger(__name__)


def log_food(telegram_user_id: int, description: str, calories: int, logged_date: str) -> bool:
    """Log a food entry. logged_date is 'YYYY-MM-DD' in the user's local time."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO calorie_entries (telegram_user_id, description, calories, logged_date)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, description.strip(), max(0, int(calories)), logged_date),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("log_food error: %s", exc)
        return False


def get_day_entries(telegram_user_id: int, logged_date: str) -> list[dict]:
    """All food entries for one day."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT description, calories FROM calorie_entries
                WHERE telegram_user_id = ? AND logged_date = ?
                ORDER BY id
                """,
                (telegram_user_id, logged_date),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_day_entries error: %s", exc)
        return []


def get_day_total(telegram_user_id: int, logged_date: str) -> int:
    """Total calories for one day."""
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(calories), 0) AS total FROM calorie_entries
                WHERE telegram_user_id = ? AND logged_date = ?
                """,
                (telegram_user_id, logged_date),
            ).fetchone()
            return int(row["total"])
    except Exception as exc:
        logger.error("get_day_total error: %s", exc)
        return 0


def get_all_entries(telegram_user_id: int, limit: int = 500) -> list[dict]:
    """All food entries, newest first (for data export)."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT description, calories, logged_date FROM calorie_entries
                WHERE telegram_user_id = ?
                ORDER BY logged_date DESC, id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_all_entries error: %s", exc)
        return []
