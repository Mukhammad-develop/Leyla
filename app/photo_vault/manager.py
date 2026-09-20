"""
Photo Vault — save and retrieve user photos by label.

Photos are stored at: <DATA_DIR>/photo_vault/<telegram_user_id>/<label>/<filename>
SQLite tracks the label -> file path mapping.
"""

import logging
import os
import shutil

from app.database.db import get_db

logger = logging.getLogger(__name__)


def _vault_dir(telegram_user_id: int, label: str) -> str:
    data_dir = os.environ.get("DATA_DIR", "data")
    safe_label = "".join(c if c.isalnum() or c in " _-" else "_" for c in label).strip()
    return os.path.join(data_dir, "photo_vault", str(telegram_user_id), safe_label)


def save_photo(
    telegram_user_id: int,
    label: str,
    src_path: str,
    file_type: str = "photo",
    file_id: str = "",
) -> bool:
    """
    Copy a file from src_path into the vault directory and record it in the DB.
    The Telegram file_id is stored alongside, so the photo can be re-sent from
    Telegram's servers even if the local copy is lost.
    If a photo with the same label already exists, it is overwritten.
    Returns True on success, False on failure.
    """
    try:
        dest_dir = _vault_dir(telegram_user_id, label)
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(src_path)[1] or ".jpg"
        dest_path = os.path.join(dest_dir, f"photo{ext}")
        shutil.copy2(src_path, dest_path)

        with get_db() as conn:
            conn.execute(
                "DELETE FROM photo_vault WHERE telegram_user_id = ? AND lower(label) = lower(?)",
                (telegram_user_id, label.strip()),
            )
            conn.execute(
                "INSERT INTO photo_vault (telegram_user_id, label, file_path, file_type, file_id) VALUES (?, ?, ?, ?, ?)",
                (telegram_user_id, label.strip(), dest_path, file_type, file_id),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("save_photo error: %s", exc)
        return False


def get_photo(telegram_user_id: int, label: str) -> str | None:
    """Return the local file path for a saved photo, or None if not found."""
    row = _find_photo_row(telegram_user_id, label)
    if row:
        path = row["file_path"]
        return path if os.path.exists(path) else None
    return None


def _find_photo_row(telegram_user_id: int, label: str) -> dict | None:
    """Exact-then-fuzzy lookup of a photo_vault row."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT file_path, file_id FROM photo_vault WHERE telegram_user_id = ? AND lower(label) = lower(?)",
                (telegram_user_id, label.strip()),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT file_path, file_id FROM photo_vault WHERE telegram_user_id = ? AND lower(label) LIKE lower(?)",
                    (telegram_user_id, f"%{label.strip()}%"),
                ).fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("_find_photo_row error: %s", exc)
        return None


def get_photo_file_id(telegram_user_id: int, label: str) -> str | None:
    """Return the Telegram file_id for a saved photo (cloud-side backup), or None."""
    row = _find_photo_row(telegram_user_id, label)
    if row and row.get("file_id"):
        return row["file_id"]
    return None


def list_photos(telegram_user_id: int) -> list[dict]:
    """Return all saved photo labels for a user."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT label, file_type, created_at FROM photo_vault WHERE telegram_user_id = ? ORDER BY created_at DESC",
                (telegram_user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("list_photos error: %s", exc)
        return []


def delete_photo(telegram_user_id: int, label: str) -> bool:
    """Delete a photo from vault and DB."""
    try:
        path = get_photo(telegram_user_id, label)
        with get_db() as conn:
            conn.execute(
                "DELETE FROM photo_vault WHERE telegram_user_id = ? AND lower(label) = lower(?)",
                (telegram_user_id, label.strip()),
            )
            conn.commit()
        if path and os.path.exists(path):
            os.unlink(path)
        return True
    except Exception as exc:
        logger.error("delete_photo error: %s", exc)
        return False
