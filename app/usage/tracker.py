"""
API cost control — per-user daily message counting and limits.

Each processed user message (text, voice, translator mode) increments a
daily counter. Once the daily limit is reached the bot replies with a
polite message instead of calling the LLM again.
"""

import logging
import os
from datetime import datetime, timezone

from app.database.db import get_db

logger = logging.getLogger(__name__)


def get_daily_limit() -> int:
    """Configured daily message limit per user. 0 means unlimited.

    Priority: value set by the admin panel (settings table) →
    DAILY_MESSAGE_LIMIT env var → 0 (unlimited).
    """
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'daily_message_limit'"
            ).fetchone()
            if row is not None:
                return max(0, int(row["value"]))
    except Exception as exc:
        logger.error("get_daily_limit DB read error: %s", exc)
    try:
        return max(0, int(os.environ.get("DAILY_MESSAGE_LIMIT", "0")))
    except ValueError:
        return 0


def set_daily_limit(limit: int) -> None:
    """Persist the daily message limit (admin panel). 0 = unlimited."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('daily_message_limit', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(max(0, int(limit))),),
        )
        conn.commit()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def increment_usage(telegram_user_id: int) -> None:
    """Count one processed message for today (UTC day)."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO usage_daily (telegram_user_id, day, message_count)
                VALUES (?, ?, 1)
                ON CONFLICT(telegram_user_id, day)
                DO UPDATE SET message_count = message_count + 1
                """,
                (telegram_user_id, _today_utc()),
            )
            conn.commit()
    except Exception as exc:
        logger.error("increment_usage error: %s", exc)


def get_usage_today(telegram_user_id: int) -> int:
    """Messages processed today (UTC day)."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT message_count FROM usage_daily WHERE telegram_user_id = ? AND day = ?",
                (telegram_user_id, _today_utc()),
            ).fetchone()
            return int(row["message_count"]) if row else 0
    except Exception as exc:
        logger.error("get_usage_today error: %s", exc)
        return 0


def is_limit_reached(telegram_user_id: int) -> bool:
    """True when the user has hit the configured daily limit."""
    limit = get_daily_limit()
    if limit <= 0:
        return False
    return get_usage_today(telegram_user_id) >= limit
