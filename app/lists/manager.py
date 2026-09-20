"""
Named lists (shopping list, to-do list, etc.) — structured per-user storage
in SQLite so nothing is lost when conversation history is truncated.
"""

import logging

from app.database.db import get_db

logger = logging.getLogger(__name__)


def add_item(telegram_user_id: int, list_name: str, item: str) -> bool:
    """Add an item to a named list. Returns True on success."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO list_items (telegram_user_id, list_name, item) VALUES (?, ?, ?)",
                (telegram_user_id, list_name.strip().lower(), item.strip()),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("add_item error: %s", exc)
        return False


def remove_item(telegram_user_id: int, list_name: str, item: str) -> bool:
    """Remove a matching item from a named list. Returns True if something was removed."""
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                DELETE FROM list_items
                WHERE telegram_user_id = ? AND list_name = ? AND lower(item) LIKE lower(?)
                """,
                (telegram_user_id, list_name.strip().lower(), f"%{item.strip()}%"),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as exc:
        logger.error("remove_item error: %s", exc)
        return False


def clear_list(telegram_user_id: int, list_name: str) -> bool:
    """Delete all items of a named list."""
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM list_items WHERE telegram_user_id = ? AND list_name = ?",
                (telegram_user_id, list_name.strip().lower()),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("clear_list error: %s", exc)
        return False


def get_all_lists(telegram_user_id: int) -> dict[str, list[str]]:
    """Return {list_name: [item, ...]} for every list of the user."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT list_name, item FROM list_items
                WHERE telegram_user_id = ?
                ORDER BY list_name, id
                """,
                (telegram_user_id,),
            ).fetchall()
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["list_name"], []).append(r["item"])
        return result
    except Exception as exc:
        logger.error("get_all_lists error: %s", exc)
        return {}
