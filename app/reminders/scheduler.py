"""
Background scheduler threads:

1. Reminder thread — polls for due reminders with ADAPTIVE sleep: it wakes
   exactly when the next reminder is due (±1s precision) instead of a fixed
   interval.
2. Daily thread — every 30s checks each user's LOCAL time and sends:
   - the morning briefing (weather + prayer times + pending reminders)
     at the user's chosen briefing time,
   - birthday / yearly-event greetings at 09:00 local.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from app.reminders.manager import (
    get_due_reminders,
    mark_reminder_completed,
    get_next_pending_time,
    get_pending_reminders,
)
from app.users.manager import get_all_users, update_briefing_last_sent
from app.events.manager import get_events_on, mark_wished
from app.hermes.adapter import parse_user_timezone

logger = logging.getLogger(__name__)

REMINDER_ALERTS = {
    "ru": "⏰ <b>Напоминание!</b>\n\n🔔 {text}",
    "en": "⏰ <b>Reminder!</b>\n\n🔔 {text}",
    "uz": "⏰ <b>Eslatma!</b>\n\n🔔 {text}",
}

BRIEFING_HEADERS = {
    "ru": "🌅 <b>Доброе утро! Вот ваш день:</b>",
    "en": "🌅 <b>Good morning! Here is your day:</b>",
    "uz": "🌅 <b>Xayrli tong! Mana bugungi kun:</b>",
}

BRIEFING_REMINDER_HEADERS = {
    "ru": "⏰ <b>Активные напоминания:</b>",
    "en": "⏰ <b>Your pending reminders:</b>",
    "uz": "⏰ <b>Faol eslatmalaringiz:</b>",
}

BRIEFING_NO_REMINDERS = {
    "ru": "⏰ Активных напоминаний нет.",
    "en": "⏰ No pending reminders.",
    "uz": "⏰ Faol eslatmalar yo'q.",
}

EVENT_GREETINGS = {
    "birthday": {
        "ru": "🎂 <b>С днём рождения!</b> Сегодня день рождения: <b>{name}</b> 🎉",
        "en": "🎂 <b>Happy Birthday!</b> Today is <b>{name}</b>'s birthday 🎉",
        "uz": "🎂 <b>Tug'ilgan kun muborak!</b> Bugun <b>{name}</b>ning tug'ilgan kuni 🎉",
    },
    "event": {
        "ru": "🗓️ Сегодня: <b>{name}</b>",
        "en": "🗓️ Today: <b>{name}</b>",
        "uz": "🗓️ Bugun: <b>{name}</b>",
    },
}

EVENT_HOUR_MINUTE = "09:00"

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _city_from_timezone(tz_str: str) -> str:
    """Derive a city name from an IANA timezone like 'Asia/Tashkent'."""
    if "/" in tz_str:
        return tz_str.split("/", 1)[1].replace("_", " ")
    return ""


def _user_local_now(user: dict):
    """datetime now in the user's timezone, or None when unknown."""
    tz_str = (user.get("timezone", "") or "").strip()
    tz = parse_user_timezone(tz_str) if tz_str else None
    if tz is None:
        return None
    return datetime.now(tz)


def _build_briefing(user: dict) -> str:
    """Compose the morning briefing: weather + prayer times + pending reminders."""
    from app.weather.forecast import get_weather
    from app.prayer.times import get_prayer_times

    telegram_id = user["telegram_user_id"]
    lang = user.get("language") or "en"
    city = (user.get("city", "") or "").strip() or _city_from_timezone(
        (user.get("timezone", "") or "").strip()
    )

    parts = [BRIEFING_HEADERS.get(lang, BRIEFING_HEADERS["en"])]

    if city:
        parts.append(get_weather(city, lang=lang))
        parts.append(get_prayer_times(city, lang=lang))
    else:
        no_city = {
            "ru": "📍 Город не указан — напишите, в каком вы городе, чтобы получать погоду и время намаза.",
            "en": "📍 No city set — tell me which city you are in to get weather and prayer times.",
            "uz": "📍 Shahar ko'rsatilmagan — ob-havo va namoz vaqtlari uchun qaysi shahardaligingizni yozing.",
        }
        parts.append(no_city.get(lang, no_city["en"]))

    pending = get_pending_reminders(telegram_id)
    if pending:
        now_ts = int(time.time())
        lines = [BRIEFING_REMINDER_HEADERS.get(lang, BRIEFING_REMINDER_HEADERS["en"])]
        for r in pending:
            diff = max(0, r["remind_at"] - now_ts)
            hours, mins = diff // 3600, (diff % 3600) // 60
            when = f"{hours}h {mins}m" if hours else f"{mins}m"
            lines.append(f"• {r['text']} — in {when}")
        parts.append("\n".join(lines))
    else:
        parts.append(BRIEFING_NO_REMINDERS.get(lang, BRIEFING_NO_REMINDERS["en"]))

    return "\n\n".join(parts)


def start_reminder_scheduler(bot) -> None:
    """Start the background scheduler threads once."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _reminder_loop():
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

                # Adaptive sleep: wake right when the next reminder is due.
                next_at = get_next_pending_time()
                if next_at is None:
                    time.sleep(30)
                else:
                    time.sleep(min(max(next_at - int(time.time()), 1), 30))
            except Exception as e:
                logger.error("Error in reminder scheduler cycle: %s", e)
                time.sleep(5)

    def _daily_loop():
        logger.info("Daily briefing/events scheduler started — running in background.")
        while True:
            try:
                for user in get_all_users():
                    if user.get("status") != "active":
                        continue
                    local_now = _user_local_now(user)
                    if local_now is None:
                        continue
                    telegram_id = user["telegram_user_id"]
                    lang = user.get("language") or "en"
                    today_str = local_now.strftime("%Y-%m-%d")
                    hhmm = local_now.strftime("%H:%M")

                    # --- Morning briefing ---
                    if (
                        user.get("briefing_enabled")
                        and hhmm == (user.get("briefing_time") or "08:00")
                        and (user.get("briefing_last_sent") or "") != today_str
                    ):
                        try:
                            briefing = _build_briefing(user)
                            bot.send_message(user["telegram_user_id"], briefing, parse_mode="HTML")
                            update_briefing_last_sent(telegram_id, today_str)
                            logger.info("Sent morning briefing to user %d", telegram_id)
                        except Exception as exc:
                            logger.error("Briefing failed for user %d: %s", telegram_id, exc)

                    # --- Birthdays / yearly events at 09:00 local ---
                    if hhmm == EVENT_HOUR_MINUTE:
                        for ev in get_events_on(telegram_id, local_now.strftime("%m-%d")):
                            if ev.get("last_wished_year", 0) >= local_now.year:
                                continue
                            try:
                                templates = EVENT_GREETINGS.get(ev["event_type"], EVENT_GREETINGS["event"])
                                greeting = templates.get(lang, templates["en"]).format(name=ev["name"])
                                bot.send_message(user["telegram_user_id"], greeting, parse_mode="HTML")
                                mark_wished(ev["id"], local_now.year)
                                logger.info("Sent event greeting '%s' to user %d", ev["name"], telegram_id)
                            except Exception as exc:
                                logger.error("Event greeting failed for user %d: %s", telegram_id, exc)
            except Exception as e:
                logger.error("Error in daily scheduler cycle: %s", e)

            time.sleep(30)

    threading.Thread(target=_reminder_loop, daemon=True, name="ReminderScheduler").start()
    threading.Thread(target=_daily_loop, daemon=True, name="DailyScheduler").start()
