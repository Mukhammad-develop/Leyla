"""
Birthday & yearly event tracker.

Events recur every year on their month-day; the daily scheduler wishes the
user (and reminds them about upcoming dates) without the LLM having to
remember anything.
"""

import logging
from datetime import date

from app.database.db import get_db

logger = logging.getLogger(__name__)


def add_event(telegram_user_id: int, name: str, event_date: str, event_type: str = "birthday") -> bool:
    """Add or update an event. event_date is 'YYYY-MM-DD'."""
    try:
        datetime_parts = event_date.strip().split("-")
        if len(datetime_parts) != 3:
            return False
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM events WHERE telegram_user_id = ? AND lower(name) = lower(?)",
                (telegram_user_id, name.strip()),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE events SET event_date = ?, event_type = ?, last_wished_year = 0 WHERE id = ?",
                    (event_date.strip(), event_type.strip() or "birthday", existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO events (telegram_user_id, name, event_date, event_type) VALUES (?, ?, ?, ?)",
                    (telegram_user_id, name.strip(), event_date.strip(), event_type.strip() or "birthday"),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("add_event error: %s", exc)
        return False


def delete_event(telegram_user_id: int, name: str) -> bool:
    """Delete an event by name (fuzzy). Returns True if something was deleted."""
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE telegram_user_id = ? AND lower(name) LIKE lower(?)",
                (telegram_user_id, f"%{name.strip()}%"),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as exc:
        logger.error("delete_event error: %s", exc)
        return False


def get_all_events(telegram_user_id: int) -> list[dict]:
    """All events of a user, soonest first (by month-day)."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, name, event_date, event_type, last_wished_year FROM events
                WHERE telegram_user_id = ?
                ORDER BY substr(event_date, 6) ASC
                """,
                (telegram_user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_all_events error: %s", exc)
        return []


def get_events_on(telegram_user_id: int, month_day: str) -> list[dict]:
    """Events whose month-day matches 'MM-DD'."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, name, event_date, event_type, last_wished_year FROM events
                WHERE telegram_user_id = ? AND substr(event_date, 6) = ?
                """,
                (telegram_user_id, month_day),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_events_on error: %s", exc)
        return []


def mark_wished(event_id: int, year: int) -> None:
    """Record that the yearly greeting for this event was sent."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE events SET last_wished_year = ? WHERE id = ?",
                (year, event_id),
            )
            conn.commit()
    except Exception as exc:
        logger.error("mark_wished error: %s", exc)


def get_upcoming_events(telegram_user_id: int, from_date: date, days: int = 40) -> list[dict]:
    """Events occurring within the next `days` days (yearly recurrence aware)."""
    upcoming = []
    for ev in get_all_events(telegram_user_id):
        try:
            month, day = int(ev["event_date"][5:7]), int(ev["event_date"][8:10])
            next_occ = date(from_date.year, month, day)
            if next_occ < from_date:
                next_occ = date(from_date.year + 1, month, day)
            delta = (next_occ - from_date).days
            if delta <= days:
                upcoming.append({**ev, "next_date": next_occ.isoformat(), "in_days": delta})
        except (ValueError, IndexError):
            continue
    upcoming.sort(key=lambda e: e["in_days"])
    return upcoming
