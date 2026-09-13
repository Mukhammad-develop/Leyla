"""
Background scheduler thread that polls for due reminders and sends them via Telegram.
"""

import time
import logging
import threading
from app.reminders.manager import get_due_reminders, mark_reminder_completed

logger = logging.getLogger(__name__)

REMINDER_ALERTS = {
    "ru": "⏰ <b>Напоминание!</b>\n\n🔔 {text}",
    "en": "⏰ <b>Reminder!</b>\n\n🔔 {text}",
    "uz": "⏰ <b>Eslatma!</b>\n\n🔔 {text}",
}

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_reminder_scheduler(bot) -> None:
    """Start the background scheduler thread once."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        logger.info("Reminder scheduler started — running in background.")
        while True:
            try:
                due = get_due_reminders(limit=20)
                for item in due:
                    chat_id = item["chat_id"]
                    text = item["text"]
                    lang = item.get("language") or "en"
                    template = REMINDER_ALERTS.get(lang, REMINDER_ALERTS["en"])
                    alert_message = template.format(text=text)

                    try:
                        bot.send_message(chat_id, alert_message, parse_mode="HTML")
                        logger.info("Sent reminder %d to chat %d", item["id"], chat_id)
                    except Exception as exc:
                        logger.error("Failed to send reminder %d to %d: %s", item["id"], chat_id, exc)
                    finally:
                        mark_reminder_completed(item["id"])
            except Exception as e:
                logger.error("Error in reminder scheduler cycle: %s", e)

            time.sleep(3)

    thread = threading.Thread(target=_loop, daemon=True, name="ReminderScheduler")
    thread.start()
