"""
Contacts Book — save and retrieve user contacts by name.
"""

import logging

from app.database.db import get_db

logger = logging.getLogger(__name__)


def save_contact(telegram_user_id: int, name: str, phone_number: str) -> bool:
    """Save or update a contact. If name already exists, update the number."""
    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE telegram_user_id = ? AND lower(name) = lower(?)",
                (telegram_user_id, name.strip()),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE contacts SET phone_number = ? WHERE id = ?",
                    (phone_number.strip(), existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO contacts (telegram_user_id, name, phone_number) VALUES (?, ?, ?)",
                    (telegram_user_id, name.strip(), phone_number.strip()),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("save_contact error: %s", exc)
        return False


def get_all_contacts(telegram_user_id: int) -> list[dict]:
    """Return all contacts for a user."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT name, phone_number FROM contacts WHERE telegram_user_id = ? ORDER BY name",
                (telegram_user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_all_contacts error: %s", exc)
        return []


def find_contact(telegram_user_id: int, name: str) -> dict | None:
    """Find a contact by name (fuzzy match)."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT name, phone_number FROM contacts WHERE telegram_user_id = ? AND lower(name) = lower(?)",
                (telegram_user_id, name.strip()),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT name, phone_number FROM contacts WHERE telegram_user_id = ? AND lower(name) LIKE lower(?)",
                    (telegram_user_id, f"%{name.strip()}%"),
                ).fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("find_contact error: %s", exc)
        return None


def delete_contact(telegram_user_id: int, name: str) -> bool:
    """Delete a contact by name."""
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM contacts WHERE telegram_user_id = ? AND lower(name) = lower(?)",
                (telegram_user_id, name.strip()),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_contact error: %s", exc)
        return False
