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


# ---------------------------------------------------------------------------
# Token-based model tiering
# ---------------------------------------------------------------------------

def get_pro_token_limit() -> int:
    """Daily pro-model token budget per user. 0 disables the pro model."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'daily_pro_token_limit'"
            ).fetchone()
            if row is not None:
                return max(0, int(row["value"]))
    except Exception as exc:
        logger.error("get_pro_token_limit DB read error: %s", exc)
    try:
        return max(0, int(os.environ.get("DAILY_PRO_TOKEN_LIMIT", "20000")))
    except ValueError:
        return 20000


def set_pro_token_limit(limit: int) -> None:
    """Persist the daily pro-model token budget (admin panel)."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('daily_pro_token_limit', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(max(0, int(limit))),),
        )
        conn.commit()


def record_token_usage(
    telegram_user_id: int,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    used_pro_model: bool,
) -> None:
    """Record LLM token usage for today (UTC day)."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO token_usage_daily
                    (telegram_user_id, day, prompt_tokens, completion_tokens, total_tokens, pro_tokens)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, day) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    pro_tokens = pro_tokens + excluded.pro_tokens
                """,
                (
                    telegram_user_id,
                    _today_utc(),
                    max(0, int(prompt_tokens)),
                    max(0, int(completion_tokens)),
                    max(0, int(total_tokens)),
                    max(0, int(total_tokens)) if used_pro_model else 0,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.error("record_token_usage error: %s", exc)


def get_token_usage_today(telegram_user_id: int) -> dict:
    """Token usage for one user today (UTC day)."""
    empty = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "pro_tokens": 0}
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT prompt_tokens, completion_tokens, total_tokens, pro_tokens
                FROM token_usage_daily WHERE telegram_user_id = ? AND day = ?
                """,
                (telegram_user_id, _today_utc()),
            ).fetchone()
            return dict(row) if row else empty
    except Exception as exc:
        logger.error("get_token_usage_today error: %s", exc)
        return empty


def get_pro_tokens_today(telegram_user_id: int) -> int:
    return int(get_token_usage_today(telegram_user_id).get("pro_tokens", 0))


def get_token_stats_today() -> dict:
    """Aggregate token usage across all users for today (UTC day)."""
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS token_users,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(pro_tokens), 0) AS pro_tokens
                FROM token_usage_daily WHERE day = ?
                """,
                (_today_utc(),),
            ).fetchone()
            total_users = int(row["token_users"] or 0)
            total_tokens = int(row["total_tokens"] or 0)
            pro_tokens = int(row["pro_tokens"] or 0)
            return {
                "token_users": total_users,
                "total_tokens": total_tokens,
                "pro_tokens": pro_tokens,
                "avg_tokens_per_token_user": round(total_tokens / total_users, 1) if total_users else 0,
            }
    except Exception as exc:
        logger.error("get_token_stats_today error: %s", exc)
        return {"token_users": 0, "total_tokens": 0, "pro_tokens": 0, "avg_tokens_per_token_user": 0}
