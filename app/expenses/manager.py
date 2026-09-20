"""
Expense tracking — durable per-user storage in SQLite.

Unlike LLM conversation history (which is truncated), expenses live in a
dedicated table, so totals stay correct over weeks and months.
"""

import logging
from datetime import datetime

from app.database.db import get_db

logger = logging.getLogger(__name__)


def add_expense(
    telegram_user_id: int,
    amount: float,
    currency: str,
    category: str,
    note: str,
    spent_date: str,
) -> bool:
    """Record an expense. spent_date is 'YYYY-MM-DD' in the user's local time."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO expenses (telegram_user_id, amount, currency, category, note, spent_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    float(amount),
                    currency.strip().upper() or "UZS",
                    category.strip(),
                    note.strip(),
                    spent_date,
                ),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("add_expense error: %s", exc)
        return False


def get_recent_expenses(telegram_user_id: int, limit: int = 10) -> list[dict]:
    """Most recent expenses, newest first."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT amount, currency, category, note, spent_date FROM expenses
                WHERE telegram_user_id = ?
                ORDER BY spent_date DESC, id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_recent_expenses error: %s", exc)
        return []


def get_month_totals(telegram_user_id: int, year_month: str) -> list[dict]:
    """Per-currency totals for a month. year_month is 'YYYY-MM'."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT currency, SUM(amount) AS total FROM expenses
                WHERE telegram_user_id = ? AND spent_date LIKE ?
                GROUP BY currency
                ORDER BY total DESC
                """,
                (telegram_user_id, f"{year_month}%"),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_month_totals error: %s", exc)
        return []
