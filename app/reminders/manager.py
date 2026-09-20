"""
Reminders & Timers manager.
Handles persistence, status updates, and retrieval of timers/reminders in SQLite.
"""

import time
import logging
from app.database.db import get_db

logger = logging.getLogger(__name__)


def add_reminder(
    telegram_user_id: int,
    chat_id: int,
    delay_seconds: int,
    text: str,
    language: str = "en",
) -> dict:
    """Add a new reminder or timer triggering after delay_seconds."""
    remind_at = int(time.time()) + max(1, delay_seconds)
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reminders (telegram_user_id, chat_id, remind_at, text, language, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (telegram_user_id, chat_id, remind_at, text.strip(), language),
        )
        conn.commit()
        reminder_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        logger.info(
            "Created reminder %d for user %d (chat %d) in %d seconds: %s",
            reminder_id,
            telegram_user_id,
            chat_id,
            delay_seconds,
            text,
        )
        return dict(row)


def get_pending_reminders(telegram_user_id: int) -> list[dict]:
    """Retrieve all active pending reminders for a given user."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM reminders 
            WHERE telegram_user_id = ? AND status = 'pending' 
            ORDER BY remind_at ASC
            """,
            (telegram_user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def cancel_reminder(telegram_user_id: int, reminder_id: int) -> bool:
    """Cancel a pending reminder. Returns True if cancelled."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders 
            SET status = 'cancelled' 
            WHERE id = ? AND telegram_user_id = ? AND status = 'pending'
            """,
            (reminder_id, telegram_user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_due_reminders(limit: int = 20) -> list[dict]:
    """Retrieve all pending reminders that are due right now."""
    now = int(time.time())
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM reminders 
            WHERE status = 'pending' AND remind_at <= ? 
            ORDER BY remind_at ASC 
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_next_pending_time() -> int | None:
    """Unix timestamp of the soonest pending reminder, or None if there are none."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT MIN(remind_at) AS next_at FROM reminders WHERE status = 'pending'"
        ).fetchone()
        return int(row["next_at"]) if row and row["next_at"] is not None else None


def count_pending() -> int:
    """Total number of pending reminders across all users."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM reminders WHERE status = 'pending'"
        ).fetchone()
        return int(row["n"])


def mark_reminder_completed(reminder_id: int) -> None:
    """Mark a reminder as completed once sent."""
    with get_db() as conn:
        conn.execute(
            "UPDATE reminders SET status = 'completed' WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()
