"""
Hermes adapter — bridges each Telegram user to an isolated
OpenRouter-backed profile with persistent memory, conversation history,
dynamic timezone detection, and real timer / reminder scheduling.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.reminders.manager import add_reminder, get_pending_reminders, cancel_reminder
from app.users.manager import (
    get_user,
    update_user_timezone,
    update_user_city,
    update_voice_mode,
    update_briefing_enabled,
    update_briefing_time,
    update_calorie_goal,
)
from app.contacts.manager import save_contact, get_all_contacts
from app.search.web import web_search, format_search_results
from app.currency.converter import convert_currency
from app.prayer.times import get_prayer_times
from app.photo_vault.manager import list_photos
from app.lists.manager import add_item, remove_item, clear_list, get_all_lists
from app.expenses.manager import add_expense, get_recent_expenses, get_month_totals
from app.calories.manager import log_food, get_day_total
from app.events.manager import add_event, delete_event, get_upcoming_events
from app.weather.forecast import get_weather

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-user locks
# ---------------------------------------------------------------------------
_user_locks: dict[str, threading.Lock] = {}
_lock_mutex = threading.Lock()


def _get_user_lock(profile_id: str) -> threading.Lock:
    with _lock_mutex:
        if profile_id not in _user_locks:
            _user_locks[profile_id] = threading.Lock()
        return _user_locks[profile_id]


# ---------------------------------------------------------------------------
# Singleton OpenRouter client (OpenAI-compatible API)
# ---------------------------------------------------------------------------
_openrouter_client = None
_openrouter_client_lock = threading.Lock()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _get_openrouter_client():
    """Return a shared OpenRouter client or None when unavailable."""
    global _openrouter_client
    if _openrouter_client is not None:
        return _openrouter_client
    with _openrouter_client_lock:
        if _openrouter_client is not None:
            return _openrouter_client
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning("OPENROUTER_API_KEY not set — LLM calls disabled")
            return None
        try:
            import openai
            _openrouter_client = openai.OpenAI(
                api_key=api_key,
                base_url=OPENROUTER_BASE_URL,
                default_headers={"X-Title": "Leyla Assistant"},
            )
            return _openrouter_client
        except Exception as exc:
            logger.error("Failed to initialise OpenRouter client: %s", exc)
            return None


def parse_user_timezone(tz_str: str):
    """Resolve an IANA timezone or UTC offset string to a tzinfo object."""
    if not tz_str:
        return None
    tz_str = tz_str.strip()
    try:
        return ZoneInfo(tz_str)
    except Exception:
        pass
    m = re.match(r"^(?:UTC|GMT)?\s*([+-]?\d{1,2})(?::?(\d{2}))?$", tz_str, re.IGNORECASE)
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2)) if m.group(2) else 0
        if hours < 0:
            minutes = -minutes
        return timezone(timedelta(hours=hours, minutes=minutes))
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
MAX_HISTORY_TURNS = 50          # messages kept on disk
CONTEXT_WINDOW_TURNS = 20       # messages sent to the model


class HermesAdapter:
    """One adapter instance per incoming message."""

    def __init__(
        self,
        profile_id: str,
        telegram_user_id: int | None = None,
        chat_id: int | None = None,
    ):
        self.profile_id = profile_id
        if telegram_user_id is None:
            try:
                self.telegram_user_id = int(profile_id.replace("telegram_", ""))
            except ValueError:
                self.telegram_user_id = 0
        else:
            self.telegram_user_id = telegram_user_id

        self.chat_id = chat_id or self.telegram_user_id
        self.data_dir = os.environ.get("DATA_DIR", "data")
        self.profile_dir = os.path.join(self.data_dir, "hermes_profiles", profile_id)
        os.makedirs(self.profile_dir, exist_ok=True)
        self.memory_file = os.path.join(self.profile_dir, "memory.json")
        self.history_file = os.path.join(self.profile_dir, "history.json")
        self.model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # ---- User-local time helpers ------------------------------------------

    def _user_tz(self):
        """Resolved tzinfo for this user, or None when unknown."""
        user = get_user(self.telegram_user_id) if self.telegram_user_id else None
        tz_str = (user.get("timezone", "") or "").strip() if user else ""
        return parse_user_timezone(tz_str) if tz_str else None

    def _user_local_today(self) -> str:
        """'YYYY-MM-DD' in the user's timezone (UTC fallback)."""
        tz = self._user_tz()
        now = datetime.now(tz) if tz else datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d")

    # ---- JSON I/O (atomic writes) ----------------------------------------

    @staticmethod
    def _read_json(path: str) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.exception("Corrupt JSON file %s — resetting", path)
            return []

    @staticmethod
    def _write_json(path: str, data) -> None:
        """Write via tmp-file + rename so a crash never leaves a half-written file."""
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ---- System prompt ----------------------------------------------------

    def _get_system_prompt(self, current_lang: str) -> str:
        name = {"ru": "Лейла", "en": "Laila", "uz": "Laylo"}.get(current_lang, "Laila")

        now_utc = datetime.now(timezone.utc)

        # Retrieve user timezone from database
        user = get_user(self.telegram_user_id) if self.telegram_user_id else None
        user_tz_str = user.get("timezone", "").strip() if user else ""
        tz_obj = parse_user_timezone(user_tz_str) if user_tz_str else None

        if tz_obj:
            user_local_now = now_utc.astimezone(tz_obj)
            tz_section = (
                f"USER LOCATION & TIMEZONE:\n"
                f"• Timezone: {user_tz_str}\n"
                f"• User Current Local Time: {user_local_now.strftime('%Y-%m-%d %H:%M:%S (%A)')}\n"
                f"• UTC Time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            reminder_tz_instructions = (
                f"• The user's timezone is ALREADY SAVED as '{user_tz_str}'.\n"
                f"• Their exact current local time is: {user_local_now.strftime('%H:%M:%S')}.\n"
                f"• When setting reminders for a specific clock time (e.g. 'at 18:00', 'tomorrow 09:00'), "
                f"use this saved timezone and calculate the exact seconds difference from their local time ({user_local_now.strftime('%H:%M')}).\n"
                "• Do NOT ask where they are or ask for confirmation again when the timezone is already saved — set the reminder immediately.\n"
                f"• If the user mentions moving or changing their city/country (e.g. 'I am in Dubai now' or 'Men Toshkentdaman'), "
                f"update their timezone by appending `[SET_TIMEZONE: <IANA_or_offset>]` and their city with `[SET_CITY: <city in English>]`.\n"
            )
        else:
            tz_section = (
                f"USER LOCATION & TIMEZONE:\n"
                f"• Timezone: NOT SET YET\n"
                f"• UTC Time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            reminder_tz_instructions = (
                "• CRITICAL RULE ON TIMEZONE / LOCATION:\n"
                "  - The user's location/timezone is NOT known yet.\n"
                "  - NEVER guess or assume where the user lives! A Russian speaker might live in Uzbekistan, Russia, Europe, or Dubai. An Uzbek speaker could live in Uzbekistan, Korea, or USA.\n"
                "  - If the user asks for a simple RELATIVE TIMER (e.g. 'timer 5 minutes', '5 minutga taymer qo\\'y', 'напомни через 10 минут'): "
                "    Relative seconds do NOT depend on timezone. Set it immediately using `[REMINDER: <seconds> | <text>]`.\n"
                "  - If the user asks for an alarm or reminder for a SPECIFIC CLOCK TIME (e.g. 'remind me at 18:00', 'напомни в 19:30', 'soat 15:00 da eslat', 'завтра утром'):\n"
                "    1. DO NOT guess their time! Ask them politely where they are located / what city or country they live in so you can alert them at the exact right moment.\n"
                "       (e.g., UZ: 'Eslatmani aniq vaqtda yuborishim uchun, qaysi shahar yoki davlatdasiz?', RU: 'Чтобы напомнить вовремя, подскажите, в каком городе вы находитесь?').\n"
                "    2. Once the user replies with their city or country (e.g. 'Toshkent', 'Ташкент', 'Moskva', 'Самарканд', 'New York', 'Dubai', 'UTC+5'):\n"
                "       - Identify the matching IANA timezone (e.g. Asia/Tashkent, Europe/Moscow, Asia/Dubai, etc.) or UTC offset.\n"
                "       - Append `[SET_TIMEZONE: <IANA_timezone_or_offset>]` to your reply.\n"
                "       - Also append `[SET_CITY: <city name in English>]` so live weather and prayer times work for them.\n"
                "       - Calculate the delay from their local time and append `[REMINDER: <seconds> | <text>]`.\n"
                "       - Warmly confirm their city/timezone has been saved and the reminder is set!\n"
            )

        prompt = (
            f"You are {name}, a highly capable, warm, and thoughtful personal AI assistant.\n"
            f"Your name is {name}.\n"
            "Imagine yourself as a helpful companion with a computer who assists the user with everyday "
            "tasks, calculations, expenses, researching, writing, and organizing their day.\n\n"
            "WHO YOU ARE & WHAT YOU CAN ACCESS (CRITICAL):\n"
            "• You are Laila, a Telegram bot. You CAN send text, photos, documents, and voice replies in Telegram.\n"
            "• NEVER say you cannot display/send images. If the user asks for a saved photo, retrieve it with `[GET_PHOTO: <label>]`.\n"
            "• NEVER tell the user to download something from a 'vault' themselves — if it is saved for them, fetch it for them.\n"
            "• You have access ONLY to this user's saved data shown in the sections below: reminders, contacts, photo vault labels, lists, expenses, calories, events, city/timezone, and chat history.\n"
            "• If something is not in those sections, say you don't have it and offer to save it — do not invent access.\n"
            "• If a saved photo label exists in 'User Photo Vault', use that exact label. If the user says they can't see the photo after you promised it, retry sending it instead of apologizing.\n\n"
            f"{tz_section}\n"
            f"The user's current preferred language code is: '{current_lang}'.\n"
            "You MUST respond primarily in that language unless the user asks to switch.\n\n"
            "BEHAVIOR & FORMATTING RULES:\n"
            "• Do NOT repeat your name (e.g. do NOT start messages with 'Men Layloman' or 'Я Лейла') in every message. "
            "Only mention your name if asked, or when greeting for the very first time.\n"
            "• Use emojis generously and naturally throughout your responses to make them warm, friendly, lively, "
            "and engaging (e.g. 😊, ✨, 💡, 📝, 🌸, 🎯, 👍, 💫, 💬, 🚀, ❤️, 📚, ☕, ⏰).\n"
            "• Use **bold** (double asterisks) for emphasis, section titles, and key terms.\n"
            "• Keep responses well-structured with bullet points, numbered lists, and clean spacing.\n\n"
            "⚙️ FUNCTION TAGS (CRITICAL SYSTEM RULE):\n"
            "• Many capabilities below work ONLY through bracketed tags such as [REMINDER: ...], [LIST_ADD: ...], "
            "[EXPENSE: ...], [LOG_FOOD: ...], [ADD_EVENT: ...], [SET_TIMEZONE: ...].\n"
            "• Tags are executed by the system and REMOVED from your message — the user never sees them.\n"
            "• NEVER claim you saved, logged, set, added, fetched, or deleted something without appending the matching tag "
            "in that SAME reply. Without the tag, the action simply does not happen.\n"
            "• NEVER say 'let me fetch', 'one moment', 'hold on', or 'just a moment' unless the matching tag is present in that SAME reply.\n"
            "• After a tag is present, finish the reply with the actual result or a clear final confirmation/error — do not stop at 'I will do it'.\n\n"
            "⏰ TIMERS & REMINDERS (NATIVE CAPABILITY):\n"
            "• You HAVE full ability to set real timers, alarms, and reminders for the user!\n"
            "• NEVER tell the user that you cannot set timers, alarms, or reminders. You CAN and MUST!\n"
            f"{reminder_tz_instructions}"
            "• When setting a timer or reminder:\n"
            "  1. Calculate the exact delay in seconds from the current time.\n"
            "     - 1 minute = 60\n"
            "     - 5 minutes = 300\n"
            "     - 10 minutes = 600\n"
            "     - 1 hour = 3600\n"
            "  2. Append the tag `[REMINDER: <seconds> | <text to remind>]` to your response.\n"
            "     Examples:\n"
            "     - User: '5 minutdan keyin suv ichishni eslat' -> Append `[REMINDER: 300 | Suv ichish]`\n"
            "     - User: 'Поставь таймер на 15 минут' -> Append `[REMINDER: 900 | Время таймера вышло! ⏰]`\n"
            "     - User: 'Remind me in 1 hour to check the oven' -> Append `[REMINDER: 3600 | Check the oven]`\n"
            "  3. Warmly and clearly confirm to the user that the timer/reminder has been set, stating how long or what time it will alert them.\n"
            "• To cancel a pending reminder if requested, append `[CANCEL_REMINDER: <id>]`.\n\n"
            "📞 CONTACTS BOOK (NATIVE CAPABILITY):\n"
            "• You can save and retrieve phone numbers for the user!\n"
            "• When user says to save a contact (e.g. 'Save my doctor's number: Aliyev, +998901234567'), append `[SAVE_CONTACT: <name> | <number>]`.\n"
            "• When user asks for a contact, check the 'User Contacts' section below and read the number from there.\n"
            "• Never tell the user you can't store contacts. You CAN!\n\n"
            "💱 LIVE CURRENCY CONVERSION (NATIVE CAPABILITY):\n"
            "• You can fetch REAL-TIME exchange rates!\n"
            "• When the user asks to convert currency (e.g. '100 dollar necha sum?', 'сколько рублей в 50 евро?', 'how much is 200 USD in UZS?'), "
            "append the tag `[CURRENCY: <FROM> | <TO> | <AMOUNT>]` to your response.\n"
            "• Examples:\n"
            "  - '100 dollar necha so'm?' -> Append `[CURRENCY: USD | UZS | 100]`\n"
            "  - 'Сколько рублей в 50 евро?' -> Append `[CURRENCY: EUR | RUB | 50]`\n"
            "  - 'Convert 200 GBP to USD' -> Append `[CURRENCY: GBP | USD | 200]`\n"
            "• The system fetches the live rate and appends it to your reply. NEVER estimate or invent an exchange rate; if there is no `[CURRENCY: ...]` tag, there is no conversion.\n\n"
            "🕌 PRAYER TIMES (NATIVE CAPABILITY):\n"
            "• You can fetch today's real prayer times for any city!\n"
            "• When the user asks for prayer times (e.g. 'Toshkentda namoz vaqtlari', 'время намаза в Москве', 'prayer times in Dubai'), "
            "append the tag `[PRAYER_TIMES: <city>]` to your response.\n"
            "• Examples:\n"
            "  - 'Toshkentda namoz vaqtlari?' -> Append `[PRAYER_TIMES: Tashkent]`\n"
            "  - 'Время намаза в Москве' -> Append `[PRAYER_TIMES: Moscow]`\n"
            "  - 'Prayer times in Dubai' -> Append `[PRAYER_TIMES: Dubai]`\n\n"
            "🔎 LIVE WEB SEARCH (NATIVE CAPABILITY):\n"
            "• You can search the internet for current information!\n"
            "• Use web search for: current news, recent events, latest research, live sports scores, today's weather, "
            "anything that might have changed recently, or when the user explicitly asks you to 'search', 'find', or 'look up'.\n"
            "• When you need to search, append `[WEB_SEARCH: <search query in English>]` to your response.\n"
            "• Examples:\n"
            "  - 'What are the latest news today?' -> Append `[WEB_SEARCH: today latest news]`\n"
            "  - 'Yangi yangiliklar qanday?' -> Append `[WEB_SEARCH: latest world news today]`\n"
            "  - 'Буратино рецепти' -> Append `[WEB_SEARCH: Buratino recipe]`\n"
            "• The search results will be injected into the conversation automatically. Summarize them for the user in their language.\n\n"
            "📸 PHOTO VAULT (NATIVE CAPABILITY):\n"
            "• Users can send you photos to save by label, and retrieve them later.\n"
            "• When the user says 'show me my [label]' or 'send my [label] photo' etc., append `[GET_PHOTO: <label>]`.\n"
            "• When the user wants to delete a saved photo, append `[DELETE_PHOTO: <label>]`.\n"
            "• The 'User Photo Vault' section below lists all saved photos.\n\n"
            "🌐 TRANSLATOR MODE:\n"
            "• If user asks to turn on translator mode (e.g. 'turn on translator to Chinese', 'переводчик на китайский', 'tarjimon rejimini yoq'):\n"
            "  1. Detect the target language from their message.\n"
            "  2. If no language mentioned, ask what language to translate to.\n"
            "  3. Append `[SET_TRANSLATOR: <lang_code>]` where lang_code is the normalized code (e.g. zh, en, ru, de, fr, ar, ko, ja, tr, uz).\n"
            "  4. Warmly confirm that translator mode is now ON.\n"
            "• If user asks to turn off translator (e.g. 'turn off translator', 'выключи переводчик'), append `[SET_TRANSLATOR: off]`.\n\n"
            "📝 LISTS — SHOPPING / TO-DO (NATIVE CAPABILITY, SAVED IN DATABASE):\n"
            "• You have REAL persistent lists stored in a database — they NEVER get forgotten, even after weeks!\n"
            "• When the user adds something to a list (e.g. 'add milk to my shopping list', 'добавь молоко в список покупок', 'xarid ro\\'yxatiga sut qo\\'sh'), "
            "append `[LIST_ADD: <list_name> | <item>]`.\n"
            "• When the user removes an item (e.g. 'remove milk from shopping list'), append `[LIST_REMOVE: <list_name> | <item>]`.\n"
            "• When the user clears a whole list (e.g. 'clear my shopping list'), append `[LIST_CLEAR: <list_name>]`.\n"
            "• Use short lowercase English list names (e.g. 'shopping', 'todo'). The current lists are in the 'User Lists' section below — read items from there when asked.\n\n"
            "💸 EXPENSE TRACKER (NATIVE CAPABILITY, SAVED IN DATABASE):\n"
            "• When the user says they spent money (e.g. 'I spent 50k som on lunch', 'потратил 20 долларов на такси', 'tushlikka 30 ming so\\'m sarfladim'), "
            "append `[EXPENSE: <amount> | <currency> | <category> | <note>]` (amount = plain number, e.g. 50000; currency = ISO code like UZS/USD).\n"
            "• Recent expenses and this month's totals are in the 'User Expenses' section below — use them to answer questions like 'how much did I spend this month?'.\n\n"
            "📊 CALORIE COUNTER (NATIVE CAPABILITY, SAVED IN DATABASE):\n"
            "• When the user tells you what they ate (e.g. 'I ate two plates of plov', 'съел борщ и хлеб', 'ikkita olma yedim'), "
            "estimate the calories yourself and append `[LOG_FOOD: <short description> | <estimated kcal number>]` (one tag per item, kcal = plain number like 450).\n"
            "• CRITICAL: The food is saved ONLY by the tag — if you do not append `[LOG_FOOD: ...]`, NOTHING is logged. "
            "NEVER claim you logged food without appending the tag to that same reply.\n"
            "• Confirm what you logged and report today's running total (see 'Calories Today' section below).\n"
            "• If the user sets a daily goal (e.g. 'my goal is 2000 kcal'), append `[CALORIE_GOAL: <number>]`.\n\n"
            "🎂 BIRTHDAYS & YEARLY EVENTS (NATIVE CAPABILITY, SAVED IN DATABASE):\n"
            "• You can remember birthdays and yearly events FOREVER and congratulate automatically every year!\n"
            "• When the user gives a birthday or yearly event (e.g. 'My mom's birthday is March 15', 'день рождения Алины 20 мая', 'Onamning tug\\'ilgan kuni 5-aprel'), "
            "append `[ADD_EVENT: <name> | <YYYY-MM-DD> | <type>]` — the date MUST be in YYYY-MM-DD format, type is 'birthday' or 'event'.\n"
            "• CRITICAL: The event is saved ONLY by the tag — if you do not append `[ADD_EVENT: ...]`, NOTHING is saved. "
            "NEVER claim you saved a birthday without appending the tag to that same reply.\n"
            "• To delete, append `[DELETE_EVENT: <name>]`.\n"
            "• Upcoming events are listed in the 'Upcoming Events' section below.\n\n"
            "🌤️ LIVE WEATHER (NATIVE CAPABILITY):\n"
            "• When the user asks about weather (e.g. 'weather in Tashkent', 'погода в Москве', 'bugun ob-havo qanday'), "
            "append `[WEATHER: <city in English>]` to your response. If no city is given and the user's city is known, use it.\n\n"
            "🎨 IMAGE GENERATION (NATIVE CAPABILITY):\n"
            "• You CAN generate real images! When the user asks to draw/create/generate a picture "
            "(e.g. 'draw a cat in space', 'нарисуй закат над горами', 'sahro rasmini chiz'), "
            "append `[GENERATE_IMAGE: <detailed English image prompt>]` and tell the user the image is being created.\n\n"
            "📦 DATA EXPORT (NATIVE CAPABILITY):\n"
            "• The user can download ALL their data (profile, memories, contacts, lists, expenses, calories, events, saved photos) as a file.\n"
            "• When the user asks to export/download/get their data (e.g. 'export my data', 'пришли мне мои данные', 'ma\\'lumotlarimni yuklab olmoqchiman'), "
            "append `[EXPORT_DATA]` and tell them the file is being prepared.\n\n"
            "🗣️ VOICE REPLY MODES:\n"
            "• The user can receive your replies as text, voice, or both.\n"
            "• If the user asks to change it (e.g. 'answer me with voice only', 'отвечай только голосом', 'faqat ovozli javob ber', 'send both text and voice'), "
            "append `[SET_VOICE_MODE: voice]`, `[SET_VOICE_MODE: text]`, or `[SET_VOICE_MODE: both]`.\n\n"
            "🌅 MORNING BRIEFING:\n"
            "• Every morning you automatically send the user weather + prayer times + pending reminders.\n"
            "• If the user wants to enable/disable it, append `[SET_BRIEFING: on]` or `[SET_BRIEFING: off]`.\n"
            "• If the user wants it at a specific time (e.g. 'send briefing at 7:30'), append `[BRIEFING_TIME: HH:MM]`.\n\n"
            "LANGUAGE SWITCHING RULES:\n"
            "• If the user EXPLICITLY asks to change the conversation language "
            "(e.g. 'Speak English', 'Давай по-русски', 'Endi o\\'zbekcha gaplashamiz'), "
            "acknowledge the change in the NEW language and append EXACTLY the tag "
            "'[LANGUAGE_CHANGED_TO: <CODE>]' at the very end of your response, where <CODE> is one of: ru, en, uz.\n"
            "• Do NOT emit this tag for any other reason.\n\n"
            "MEMORY RULES:\n"
            "• When the user tells you a fact to remember, store it by appending '[REMEMBER: <fact>]' to your response.\n"
            "• Use the memory context below to personalise answers.\n"
        )

        # Inject pending reminders if any exist
        try:
            pending = get_pending_reminders(self.telegram_user_id)
            if pending:
                now_ts = int(time.time())
                rem_lines = []
                for r in pending:
                    diff = max(0, r["remind_at"] - now_ts)
                    mins = diff // 60
                    secs = diff % 60
                    time_desc = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    rem_lines.append(f"- ID {r['id']}: in ~{time_desc}: '{r['text']}'")
                prompt += "\nActive Pending Reminders for this user:\n" + "\n".join(rem_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load pending reminders for prompt: %s", e)

        # Inject contacts context
        try:
            contacts = get_all_contacts(self.telegram_user_id)
            if contacts:
                c_lines = [f"- {c['name']}: {c['phone_number']}" for c in contacts]
                prompt += "\nUser Contacts:\n" + "\n".join(c_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load contacts for prompt: %s", e)

        # Inject photo vault context
        try:
            photos = list_photos(self.telegram_user_id)
            if photos:
                p_lines = [f"- {p['label']} ({p['file_type']})" for p in photos]
                prompt += "\nUser Photo Vault (saved photos/documents):\n" + "\n".join(p_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load photo vault for prompt: %s", e)

        # Inject user lists (shopping, todo, ...)
        try:
            user_lists = get_all_lists(self.telegram_user_id)
            if user_lists:
                l_lines = [f"- {name}: {', '.join(items)}" for name, items in user_lists.items()]
                prompt += "\nUser Lists (stored in database, always up to date):\n" + "\n".join(l_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load lists for prompt: %s", e)

        # Inject expenses: this month's totals + recent entries
        try:
            today = self._user_local_today()
            totals = get_month_totals(self.telegram_user_id, today[:7])
            recent = get_recent_expenses(self.telegram_user_id, limit=10)
            if totals or recent:
                exp_lines = []
                if totals:
                    total_str = ", ".join(f"{t['total']:,.0f} {t['currency']}" for t in totals)
                    exp_lines.append(f"This month ({today[:7]}) total: {total_str}")
                for r in recent:
                    desc = r["note"] or r["category"] or "expense"
                    exp_lines.append(f"- {r['spent_date']}: {r['amount']:,.0f} {r['currency']} ({desc})")
                prompt += "\nUser Expenses (stored in database):\n" + "\n".join(exp_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load expenses for prompt: %s", e)

        # Inject today's calorie total
        try:
            user = get_user(self.telegram_user_id) if self.telegram_user_id else None
            goal = int(user.get("calorie_goal", 0) or 0) if user else 0
            today_total = get_day_total(self.telegram_user_id, self._user_local_today())
            if today_total > 0 or goal > 0:
                goal_str = f" / goal {goal}" if goal > 0 else ""
                prompt += f"\nCalories Today: {today_total} kcal{goal_str}\n"
        except Exception as e:
            logger.debug("Could not load calories for prompt: %s", e)

        # Inject upcoming birthdays / events
        try:
            tz = self._user_tz()
            today_d = datetime.now(tz).date() if tz else datetime.now(timezone.utc).date()
            upcoming = get_upcoming_events(self.telegram_user_id, today_d, days=40)
            if upcoming:
                ev_lines = [f"- {e['name']} ({e['event_type']}): {e['next_date']} (in {e['in_days']} days)" for e in upcoming]
                prompt += "\nUpcoming Events (birthdays etc., saved in database):\n" + "\n".join(ev_lines) + "\n"
        except Exception as e:
            logger.debug("Could not load events for prompt: %s", e)

        # Inject known city for weather defaults
        try:
            user = get_user(self.telegram_user_id) if self.telegram_user_id else None
            city = (user.get("city", "") or "").strip() if user else ""
            if city:
                prompt += f"\nUser's City: {city} (use as default for weather/prayer requests)\n"
        except Exception as e:
            logger.debug("Could not load city for prompt: %s", e)

        return prompt

    @staticmethod
    def _looks_like_image_request(text: str) -> bool:
        low = text.lower()
        return bool(
            re.search(r"\b(draw|paint|sketch|illustrate)\b", low)
            or re.search(r"\b(generate|create|make)\b.{0,50}\b(image|picture|photo|art|logo|poster)\b", low)
            or re.search(r"нарис|картинк|изображени", low)
            or re.search(r"(rasm|surat).{0,25}(chiz|yarat|tashla)", low)
            or re.search(r"(chiz|yarat).{0,25}(rasm|surat)", low)
        )

    @staticmethod
    def _money_code(token: str) -> str | None:
        t = token.strip().lower().strip("'’")
        iso = {"USD", "EUR", "UZS", "RUB", "GBP", "KZT", "TRY", "CNY", "JPY", "KRW", "AED", "INR"}
        if t.upper() in iso:
            return t.upper()
        return {
            "$": "USD", "dollar": "USD", "dollars": "USD", "buck": "USD", "bucks": "USD",
            "€": "EUR", "euro": "EUR", "euros": "EUR",
            "sum": "UZS", "som": "UZS", "so'm": "UZS", "so`m": "UZS",
            "ruble": "RUB", "rubles": "RUB", "rubl": "RUB",
            "pound": "GBP", "pounds": "GBP",
        }.get(t)

    @classmethod
    def _currency_request(cls, text: str):
        low = text.lower()
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*([a-z$€'’]{1,14})\s*(?:to|into|in)\s*([a-z$€'’]{1,14})", low)
        if not m:
            return None
        from_code = cls._money_code(m.group(2))
        to_code = cls._money_code(m.group(3))
        if not from_code or not to_code or from_code == to_code:
            return None
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        return from_code, to_code, amount

    @staticmethod
    def _web_search_query(text: str) -> str | None:
        low = text.lower().strip()
        triggers = ("search", "look up", "find online", "latest news", "news today", "current news", "web search")
        if not any(t in low for t in triggers):
            return None
        query = re.sub(r"^(please\s+)?(search( the web)? for|look up|find online|web search for)\s*", "", low).strip()
        return query or text.strip()

    @staticmethod
    def _looks_like_export(text: str) -> bool:
        low = text.lower()
        return bool(
            ("export" in low and "data" in low)
            or "send me my data" in low
            or "download my data" in low
            or "мои данные" in low
            or "ma'lumotlarim" in low
        )

    @staticmethod
    def _photo_get_label(text: str) -> str | None:
        low = text.lower()
        patterns = (
            r"send me my (.+?) photo", r"send my (.+?) photo", r"show me my (.+?) photo",
            r"get my (.+?) photo", r"retrieve my (.+?) photo", r"give me my (.+?) photo",
        )
        for pattern in patterns:
            m = re.search(pattern, low)
            if m:
                return m.group(1).strip(" .'\"")
        return None

    @staticmethod
    def _photo_delete_label(text: str) -> str | None:
        low = text.lower()
        for pattern in (r"delete my (.+?) photo", r"delete (.+?) photo", r"remove my (.+?) photo"):
            m = re.search(pattern, low)
            if m:
                return m.group(1).strip(" .'\"")
        return None

    @staticmethod
    def _photo_complaint(text: str) -> bool:
        low = text.lower()
        return bool(
            ("can't see" in low or "cant see" in low or "cannot see" in low or "don't see" in low or "do not see" in low)
            and ("photo" in low or "picture" in low or "image" in low)
        ) or ("no photo" in low) or ("where is the photo" in low) or ("where's the photo" in low)

    @staticmethod
    def _normalize_lang_code(value: str) -> str:
        low = value.strip().lower()
        if low in {"off", "none", "disable", "stop"}:
            return "off"
        mapping = {
            "chinese": "zh", "zh": "zh", "mandarin": "zh",
            "english": "en", "en": "en",
            "russian": "ru", "ru": "ru",
            "uzbek": "uz", "uz": "uz",
            "german": "de", "de": "de",
            "french": "fr", "fr": "fr",
            "spanish": "es", "es": "es",
            "italian": "it", "it": "it",
            "arabic": "ar", "ar": "ar",
            "korean": "ko", "ko": "ko",
            "japanese": "ja", "ja": "ja",
            "turkish": "tr", "tr": "tr",
        }
        return mapping.get(low, low[:2])

    @classmethod
    def _translator_request(cls, text: str) -> str | None:
        low = text.lower()
        if "translator" not in low and "перевод" not in low and "tarjimon" not in low:
            return None
        if " off" in low or "turn off" in low or "disable" in low or "выключ" in low or "o'chir" in low:
            return "off"
        if not (" on" in low or "turn on" in low or "enable" in low or " to " in low or "на " in low or "ga " in low):
            return None
        for name in ("chinese", "mandarin", "english", "russian", "uzbek", "german", "french", "spanish", "italian", "arabic", "korean", "japanese", "turkish"):
            if name in low:
                return cls._normalize_lang_code(name)
        m = re.search(r"\b(zh|en|ru|uz|de|fr|es|it|ar|ko|ja|tr)\b", low)
        return m.group(1) if m else None

    @staticmethod
    def _voice_mode_request(text: str) -> str | None:
        low = text.lower()
        if "text and voice" in low or "voice and text" in low or "both text and voice" in low or "both voice and text" in low:
            return "both"
        if "voice only" in low or "only voice" in low or "answer with voice" in low or "reply with voice" in low or "отвечай голосом" in low or "ovozli javob" in low:
            return "voice"
        if "text only" in low or "only text" in low or "answer with text" in low or "reply with text" in low or "отвечай текстом" in low or "matn bilan javob" in low:
            return "text"
        return None

    @staticmethod
    def _relative_reminder_request(text: str):
        low = text.lower()
        m = re.search(
            r"(?:set (?:a )?timer for|remind me in| напомни через | eslat )\s*(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|секунд\w*|минут\w*|час\w*|soat|daqiqa|soniya)\s*(?:to\s+(.+))?",
            low,
        )
        if not m:
            return None
        amount = int(m.group(1))
        unit = m.group(2)
        seconds = amount
        if unit.startswith(("min", "мин", "daqiqa")):
            seconds = amount * 60
        elif unit.startswith(("hour", "hr", "час", "soat")):
            seconds = amount * 3600
        reminder_text = (m.group(3) or "Timer").strip(" .")
        return seconds, reminder_text

    @staticmethod
    def _parse_event_date(value: str) -> str | None:
        value = value.strip()
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value)
        if m:
            return m.group(1)
        for fmt in ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d %B, %Y"):
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        m = re.search(r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", value)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} 1990", "%B %d %Y").strftime("%Y-%m-%d")
            except ValueError:
                return None
        return None

    @classmethod
    def _event_add_request(cls, text: str):
        low = text.lower()
        if "birthday is" not in low and "birthday" not in low:
            return None
        if "birthday is" in low:
            name_part, date_part = re.split(r"birthday is", low, maxsplit=1)
        else:
            m = re.search(r"(.+?)(?:['’]s)?\s+birthday[:]?\s*(.+)", low)
            if not m:
                return None
            name_part, date_part = m.group(1), m.group(2)
        name = re.sub(r"^(my|our)\s+", "", name_part).strip(" .'\"")
        name = re.sub(r"(friend|mom|dad|mother|father|brother|sister)\s+", "", name).strip(" .'\"") or name_part.strip(" .'\"")
        event_date = cls._parse_event_date(date_part)
        if not name or not event_date:
            return None
        return name, event_date, "birthday"

    @staticmethod
    def _event_delete_request(text: str) -> str | None:
        low = text.lower()
        m = re.search(r"delete event\s+(.+)", low)
        if m:
            return m.group(1).strip(" .'\"")
        m = re.search(r"delete\s+(.+?)(?:['’]s)?\s+birthday", low)
        if m:
            return m.group(1).strip(" .'\"")
        return None

    def _apply_action_fallbacks(self, message: str, response: str, current_lang: str, handled: dict, history: list) -> str:
        """Run obvious native actions when the model promised them but forgot the tag."""
        notes = []

        if not handled.get("currency"):
            req = self._currency_request(message)
            if req:
                from_code, to_code, amount = req
                try:
                    response = (response + "\n\n" + convert_currency(from_code, to_code, amount)).strip()
                except Exception as exc:
                    logger.error("Fallback currency conversion failed: %s", exc)
                    notes.append({
                        "ru": "❌ Не удалось получить живой курс сейчас. Попробуйте ещё раз.",
                        "uz": "❌ Hozir jonli kursni olib bo'lmadi. Qaytadan urinib ko'ring.",
                        "en": "❌ I couldn't fetch the live rate right now. Please try again.",
                    }.get(current_lang, "❌ I couldn't fetch the live rate right now. Please try again."))

        if not handled.get("search"):
            query = self._web_search_query(message)
            if query:
                try:
                    results = web_search(query)
                    response = (response + "\n\n" + format_search_results(results, lang=current_lang)).strip()
                except Exception as exc:
                    logger.error("Fallback web search failed: %s", exc)
                    notes.append({
                        "ru": "❌ Поиск сейчас не сработал. Попробуйте ещё раз.",
                        "uz": "❌ Qidiruv hozir ishlamadi. Qaytadan urinib ko'ring.",
                        "en": "❌ Search failed right now. Please try again.",
                    }.get(current_lang, "❌ Search failed right now. Please try again."))

        if not handled.get("translator"):
            translator = self._translator_request(message)
            if translator:
                self._pending_translator_update = translator
                if translator == "off":
                    notes.append({
                        "ru": "🌐 Переводчик выключен.",
                        "uz": "🌐 Tarjimon o'chirildi.",
                        "en": "🌐 Translator is off.",
                    }.get(current_lang, "🌐 Translator is off."))
                else:
                    notes.append({
                        "ru": f"🌐 Переводчик включён: {translator}.",
                        "uz": f"🌐 Tarjimon yoqildi: {translator}.",
                        "en": f"🌐 Translator is on: {translator}.",
                    }.get(current_lang, f"🌐 Translator is on: {translator}."))

        if not handled.get("voice"):
            mode = self._voice_mode_request(message)
            if mode:
                try:
                    update_voice_mode(self.telegram_user_id, mode)
                except Exception as exc:
                    logger.error("Fallback voice mode failed: %s", exc)

        if not handled.get("get_photo"):
            label = self._photo_get_label(message)
            if not label and self._photo_complaint(message):
                try:
                    photos = list_photos(self.telegram_user_id)
                    if len(photos) == 1:
                        label = photos[0]["label"]
                    elif photos:
                        recent_text = "\n".join((m.get("content") or "") for m in history[-12:]).lower()
                        matches = [p["label"] for p in photos if p["label"].lower() in recent_text]
                        if matches:
                            label = max(matches, key=lambda item: recent_text.rfind(item.lower()))
                except Exception as exc:
                    logger.error("Fallback photo retry failed: %s", exc)
            if label:
                self._pending_get_photo = label

        if not handled.get("delete_photo"):
            label = self._photo_delete_label(message)
            if label:
                self._pending_delete_photo = label

        if not handled.get("export") and self._looks_like_export(message):
            self._pending_export = True

        if not handled.get("reminder"):
            reminder = self._relative_reminder_request(message)
            if reminder:
                seconds, text = reminder
                try:
                    add_reminder(
                        telegram_user_id=self.telegram_user_id,
                        chat_id=self.chat_id,
                        delay_seconds=seconds,
                        text=text,
                        language=current_lang,
                    )
                    if seconds >= 3600:
                        when = f"{seconds // 3600}h {(seconds % 3600) // 60}m"
                    elif seconds >= 60:
                        when = f"{seconds // 60}m {seconds % 60}s"
                    else:
                        when = f"{seconds}s"
                    notes.append({
                        "ru": f"⏰ Напоминание установлено: {text} — через {when}.",
                        "uz": f"⏰ Eslatma o'rnatildi: {text} — {when}dan keyin.",
                        "en": f"⏰ Reminder set: {text} — in {when}.",
                    }.get(current_lang, f"⏰ Reminder set: {text} — in {when}."))
                except Exception as exc:
                    logger.error("Fallback reminder failed: %s", exc)

        if not handled.get("event_add"):
            event = self._event_add_request(message)
            if event:
                name, event_date, event_type = event
                try:
                    add_event(self.telegram_user_id, name, event_date, event_type)
                    notes.append({
                        "ru": f"🎂 Сохранено: {name} — {event_date}.",
                        "uz": f"🎂 Saqlandi: {name} — {event_date}.",
                        "en": f"🎂 Saved: {name} — {event_date}.",
                    }.get(current_lang, f"🎂 Saved: {name} — {event_date}."))
                except Exception as exc:
                    logger.error("Fallback add event failed: %s", exc)

        if not handled.get("event_delete"):
            name = self._event_delete_request(message)
            if name:
                try:
                    if delete_event(self.telegram_user_id, name):
                        notes.append({
                            "ru": f"🗑️ Удалено: {name}.",
                            "uz": f"🗑️ O'chirildi: {name}.",
                            "en": f"🗑️ Deleted: {name}.",
                        }.get(current_lang, f"🗑️ Deleted: {name}."))
                    else:
                        notes.append({
                            "ru": f"Не нашёл событие: {name}.",
                            "uz": f"Topilmadi: {name}.",
                            "en": f"I couldn't find an event named: {name}.",
                        }.get(current_lang, f"I couldn't find an event named: {name}."))
                except Exception as exc:
                    logger.error("Fallback delete event failed: %s", exc)

        if notes:
            response = (response + "\n\n" + "\n".join(notes)).strip()
        return response


    # ---- Public entry point -----------------------------------------------

    def send_message(self, message: str, current_lang: str) -> str:
        """Thread-safe: acquire per-user lock → call LLM → persist state."""
        lock = _get_user_lock(self.profile_id)
        with lock:
            history = self._read_json(self.history_file)
            memory = self._read_json(self.memory_file)

            client = _get_openrouter_client()
            if client is None:
                response = self._mock_llm_response(message, current_lang, memory)
            else:
                response = self._openai_response(
                    client, message, current_lang, history, memory,
                )

            # Process [SET_TIMEZONE: <tz>] tags
            tz_matches = re.findall(r"\[SET_TIMEZONE:\s*(.+?)\]", response)
            for tz_val in tz_matches:
                try:
                    update_user_timezone(self.telegram_user_id, tz_val.strip())
                    logger.info("Updated timezone for user %d to %s", self.telegram_user_id, tz_val.strip())
                except Exception as exc:
                    logger.error("Failed to update user timezone: %s", exc)
            response = re.sub(r"\[SET_TIMEZONE:\s*.+?\]", "", response).strip()

            # Process [REMINDER: <seconds> | <text>] tags
            rem_matches = re.findall(r"\[REMINDER:\s*(\d+)\s*\|\s*(.+?)\]", response)
            for delay_str, text_val in rem_matches:
                try:
                    delay_sec = int(delay_str)
                    add_reminder(
                        telegram_user_id=self.telegram_user_id,
                        chat_id=self.chat_id,
                        delay_seconds=delay_sec,
                        text=text_val.strip(),
                        language=current_lang,
                    )
                except Exception as exc:
                    logger.error("Failed to schedule reminder from response: %s", exc)
            response = re.sub(r"\[REMINDER:\s*\d+\s*\|\s*.+?\]", "", response).strip()

            # Process [CANCEL_REMINDER: <id>] tags
            cancel_matches = re.findall(r"\[CANCEL_REMINDER:\s*(\d+)\]", response)
            for cid_str in cancel_matches:
                try:
                    cancel_reminder(self.telegram_user_id, int(cid_str))
                except Exception as exc:
                    logger.error("Failed to cancel reminder: %s", exc)
            response = re.sub(r"\[CANCEL_REMINDER:\s*\d+\]", "", response).strip()

            # Process [SAVE_CONTACT: <name> | <number>] tags
            contact_matches = re.findall(r"\[SAVE_CONTACT:\s*(.+?)\s*\|\s*(.+?)\]", response)
            for c_name, c_phone in contact_matches:
                try:
                    save_contact(self.telegram_user_id, c_name.strip(), c_phone.strip())
                    logger.info("Saved contact '%s' for user %d", c_name, self.telegram_user_id)
                except Exception as exc:
                    logger.error("Failed to save contact: %s", exc)
            response = re.sub(r"\[SAVE_CONTACT:\s*.+?\]", "", response).strip()

            # Process [CURRENCY: <FROM> | <TO> | <AMOUNT>] tags — inject live rate
            currency_matches = re.findall(r"\[CURRENCY:\s*([A-Za-z]+)\s*\|\s*([A-Za-z]+)\s*\|\s*([\d.,]+)\]", response)
            response = re.sub(r"\[CURRENCY:\s*.+?\]", "", response).strip()
            for from_c, to_c, amount_s in currency_matches:
                try:
                    amount = float(amount_s.replace(",", ""))
                    rate_result = convert_currency(from_c.strip(), to_c.strip(), amount)
                    response = response + "\n\n" + rate_result
                except Exception as exc:
                    logger.error("Currency conversion failed: %s", exc)

            # Process [PRAYER_TIMES: <city>] tags
            prayer_matches = re.findall(r"\[PRAYER_TIMES:\s*(.+?)\]", response)
            response = re.sub(r"\[PRAYER_TIMES:\s*.+?\]", "", response).strip()
            for city in prayer_matches:
                try:
                    prayer_result = get_prayer_times(city.strip(), lang=current_lang)
                    response = response + "\n\n" + prayer_result
                except Exception as exc:
                    logger.error("Prayer times fetch failed: %s", exc)

            # Process [WEB_SEARCH: <query>] tags
            search_matches = re.findall(r"\[WEB_SEARCH:\s*(.+?)\]", response)
            response = re.sub(r"\[WEB_SEARCH:\s*.+?\]", "", response).strip()
            for query in search_matches:
                try:
                    results = web_search(query.strip())
                    search_text = format_search_results(results, lang=current_lang)
                    response = response + "\n\n" + search_text
                except Exception as exc:
                    logger.error("Web search failed: %s", exc)

            # Process [SET_TRANSLATOR: <lang>] tags — store on the adapter for bot.py to pick up
            translator_matches = re.findall(r"\[SET_TRANSLATOR:\s*(.+?)\]", response)
            self._pending_translator_update = None
            if translator_matches:
                val = translator_matches[-1].strip()
                self._pending_translator_update = self._normalize_lang_code(val)  # 'off' or a lang code
            response = re.sub(r"\[SET_TRANSLATOR:\s*.+?\]", "", response).strip()

            # Process [GET_PHOTO: <label>] and [DELETE_PHOTO: <label>] — store for bot.py
            get_photo_matches = re.findall(r"\[GET_PHOTO:\s*(.+?)\]", response)
            self._pending_get_photo = get_photo_matches[-1].strip() if get_photo_matches else None
            response = re.sub(r"\[GET_PHOTO:\s*.+?\]", "", response).strip()

            delete_photo_matches = re.findall(r"\[DELETE_PHOTO:\s*(.+?)\]", response)
            self._pending_delete_photo = delete_photo_matches[-1].strip() if delete_photo_matches else None
            response = re.sub(r"\[DELETE_PHOTO:\s*.+?\]", "", response).strip()

            # Process [SET_CITY: <city>] tags
            city_matches = re.findall(r"\[SET_CITY:\s*(.+?)\]", response)
            for city_val in city_matches:
                try:
                    update_user_city(self.telegram_user_id, city_val.strip())
                    logger.info("Updated city for user %d to %s", self.telegram_user_id, city_val.strip())
                except Exception as exc:
                    logger.error("Failed to update user city: %s", exc)
            response = re.sub(r"\[SET_CITY:\s*.+?\]", "", response).strip()

            # Process list tags: [LIST_ADD: <list> | <item>], [LIST_REMOVE: ...], [LIST_CLEAR: <list>]
            for list_name, item in re.findall(r"\[LIST_ADD:\s*(.+?)\s*\|\s*(.+?)\]", response):
                try:
                    add_item(self.telegram_user_id, list_name, item)
                    logger.info("List add for user %d: %s | %s", self.telegram_user_id, list_name, item)
                except Exception as exc:
                    logger.error("Failed to add list item: %s", exc)
            response = re.sub(r"\[LIST_ADD:\s*.+?\]", "", response).strip()

            for list_name, item in re.findall(r"\[LIST_REMOVE:\s*(.+?)\s*\|\s*(.+?)\]", response):
                try:
                    remove_item(self.telegram_user_id, list_name, item)
                except Exception as exc:
                    logger.error("Failed to remove list item: %s", exc)
            response = re.sub(r"\[LIST_REMOVE:\s*.+?\]", "", response).strip()

            for list_name in re.findall(r"\[LIST_CLEAR:\s*(.+?)\]", response):
                try:
                    clear_list(self.telegram_user_id, list_name)
                except Exception as exc:
                    logger.error("Failed to clear list: %s", exc)
            response = re.sub(r"\[LIST_CLEAR:\s*.+?\]", "", response).strip()

            # Process [EXPENSE: <amount> | <currency> | <category> | <note>] tags
            expense_matches = re.findall(r"\[EXPENSE:\s*([\d.,]+)\s*\|\s*([A-Za-z]+)\s*\|\s*([^|\]]+?)\s*\|\s*(.+?)\]", response)
            for amount_s, cur_s, cat_s, note_s in expense_matches:
                try:
                    amount = float(amount_s.replace(",", "").replace(" ", ""))
                    add_expense(
                        self.telegram_user_id,
                        amount,
                        cur_s.strip(),
                        cat_s.strip(),
                        note_s.strip(),
                        self._user_local_today(),
                    )
                    logger.info("Logged expense for user %d: %s %s", self.telegram_user_id, amount_s, cur_s)
                except Exception as exc:
                    logger.error("Failed to log expense: %s", exc)
            response = re.sub(r"\[EXPENSE:\s*.+?\]", "", response).strip()

            # Process [LOG_FOOD: <description> | <kcal>] tags
            food_matches = re.findall(r"\[LOG_FOOD:\s*(.+?)\s*\|\s*(\d+)\s*\]", response)
            response = re.sub(r"\[LOG_FOOD:\s*.+?\]", "", response).strip()
            for desc, kcal in food_matches:
                try:
                    log_food(self.telegram_user_id, desc.strip(), int(kcal), self._user_local_today())
                    logger.info("Logged food for user %d: %s (%s kcal)", self.telegram_user_id, desc, kcal)
                except Exception as exc:
                    logger.error("Failed to log food: %s", exc)

            # Process [CALORIE_GOAL: <number>] tags
            goal_matches = re.findall(r"\[CALORIE_GOAL:\s*(\d+)\]", response)
            for goal_s in goal_matches:
                try:
                    update_calorie_goal(self.telegram_user_id, int(goal_s))
                except Exception as exc:
                    logger.error("Failed to set calorie goal: %s", exc)
            response = re.sub(r"\[CALORIE_GOAL:\s*\d+\]", "", response).strip()

            # Process [ADD_EVENT: <name> | <YYYY-MM-DD> | <type>] tags (type optional)
            event_matches = re.findall(r"\[ADD_EVENT:\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*(?:\|\s*(.+?))?\]", response)
            for ev_name, ev_date, ev_type in event_matches:
                try:
                    add_event(self.telegram_user_id, ev_name.strip(), ev_date.strip(), (ev_type or "birthday").strip())
                    logger.info("Added event for user %d: %s on %s", self.telegram_user_id, ev_name, ev_date)
                except Exception as exc:
                    logger.error("Failed to add event: %s", exc)
            response = re.sub(r"\[ADD_EVENT:\s*.+?\]", "", response).strip()

            # Process [DELETE_EVENT: <name>] tags
            delete_event_matches = re.findall(r"\[DELETE_EVENT:\s*(.+?)\]", response)
            for ev_name in delete_event_matches:
                try:
                    delete_event(self.telegram_user_id, ev_name.strip())
                except Exception as exc:
                    logger.error("Failed to delete event: %s", exc)
            response = re.sub(r"\[DELETE_EVENT:\s*.+?\]", "", response).strip()

            # Process [WEATHER: <city>] tags — inject live weather
            weather_matches = re.findall(r"\[WEATHER:\s*(.+?)\]", response)
            response = re.sub(r"\[WEATHER:\s*.+?\]", "", response).strip()
            for city_q in weather_matches:
                try:
                    weather_text = get_weather(city_q.strip(), lang=current_lang)
                    response = response + "\n\n" + weather_text
                except Exception as exc:
                    logger.error("Weather fetch failed: %s", exc)

            # Process [GENERATE_IMAGE: <prompt>] — store for bot.py
            image_matches = re.findall(r"\[GENERATE_IMAGE:\s*([\s\S]+?)\]", response)
            self._pending_image_prompt = image_matches[-1].strip() if image_matches else None
            response = re.sub(r"\[GENERATE_IMAGE:\s*[\s\S]+?\]", "", response).strip()
            if not self._pending_image_prompt and self._looks_like_image_request(message):
                # The model sometimes says "I'm generating..." but forgets the tag.
                self._pending_image_prompt = message.strip()

            # Process [EXPORT_DATA] — store for bot.py
            self._pending_export = bool(re.search(r"\[EXPORT_DATA\]", response))
            response = re.sub(r"\[EXPORT_DATA\]", "", response).strip()

            # Process [SET_VOICE_MODE: voice|text|both] tags
            voice_mode_matches = re.findall(r"\[SET_VOICE_MODE:\s*(voice|text|both)\]", response, re.IGNORECASE)
            for mode_val in voice_mode_matches:
                try:
                    update_voice_mode(self.telegram_user_id, mode_val.lower())
                    logger.info("Voice mode for user %d set to %s", self.telegram_user_id, mode_val)
                except Exception as exc:
                    logger.error("Failed to set voice mode: %s", exc)
            response = re.sub(r"\[SET_VOICE_MODE:\s*.+?\]", "", response).strip()

            # Process [SET_BRIEFING: on|off] and [BRIEFING_TIME: HH:MM] tags
            briefing_matches = re.findall(r"\[SET_BRIEFING:\s*(on|off)\]", response, re.IGNORECASE)
            for flag in briefing_matches:
                try:
                    update_briefing_enabled(self.telegram_user_id, flag.lower() == "on")
                except Exception as exc:
                    logger.error("Failed to set briefing: %s", exc)
            response = re.sub(r"\[SET_BRIEFING:\s*.+?\]", "", response).strip()

            btime_matches = re.findall(r"\[BRIEFING_TIME:\s*(\d{1,2}:\d{2})\]", response)
            for t_val in btime_matches:
                try:
                    update_briefing_time(self.telegram_user_id, t_val)
                except Exception as exc:
                    logger.error("Failed to set briefing time: %s", exc)
            response = re.sub(r"\[BRIEFING_TIME:\s*\d{1,2}:\d{2}\]", "", response).strip()

            response = self._apply_action_fallbacks(
                message,
                response,
                current_lang,
                handled={
                    "currency": bool(currency_matches),
                    "search": bool(search_matches),
                    "translator": bool(translator_matches),
                    "voice": bool(voice_mode_matches),
                    "get_photo": bool(get_photo_matches) or bool(self._pending_get_photo),
                    "delete_photo": bool(delete_photo_matches) or bool(self._pending_delete_photo),
                    "export": bool(self._pending_export),
                    "reminder": bool(rem_matches),
                    "event_add": bool(event_matches),
                    "event_delete": bool(delete_event_matches),
                },
                history=history,
            )

            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": response})
            self._write_json(self.history_file, history[-MAX_HISTORY_TURNS:])
            self._write_json(self.memory_file, memory)
            return response


    # ---- OpenAI call ------------------------------------------------------

    def _openai_response(self, client, message, current_lang, history, memory):
        system_prompt = self._get_system_prompt(current_lang)
        if memory:
            system_prompt += "\nUser Memory Context:\n" + "\n".join(
                f"- {m['value']}" for m in memory if "value" in m
            )

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-CONTEXT_WINDOW_TURNS:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        try:
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
            reply = completion.choices[0].message.content or ""

            # Extract [REMEMBER: ...] tags and persist them
            for fact in re.findall(r"\[REMEMBER:\s*(.+?)\]", reply):
                memory.append({"type": "fact", "value": fact})
            reply = re.sub(r"\[REMEMBER:\s*.+?\]", "", reply).strip()

            return reply
        except Exception as exc:
            logger.error("OpenRouter API error for %s: %s", self.profile_id, exc)
            error_msgs = {
                "ru": "Произошла ошибка при обработке запроса. Попробуйте ещё раз.",
                "en": "Something went wrong. Please try again.",
                "uz": "Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            }
            return error_msgs.get(current_lang, error_msgs["en"])

    # ---- Mock (used in tests / when no API key) ---------------------------

    def _mock_llm_response(self, message, current_lang, memory):
        low = message.lower()
        if "speak english" in low:
            return "Okay, I will speak English now. [LANGUAGE_CHANGED_TO: en]"
        if "по-русски" in low or "русский" in low:
            return "Хорошо, перехожу на русский. [LANGUAGE_CHANGED_TO: ru]"
        if "o'zbekcha" in low or "o'zbek" in low:
            return "Xo'p, endi o'zbekchada gaplashaman. [LANGUAGE_CHANGED_TO: uz]"
        if "toshkent" in low or "tashkent" in low or "ташкент" in low:
            return "Toshkent vaqti saqlandi! [SET_TIMEZONE: Asia/Tashkent] [SET_CITY: Tashkent]"
        if "remind me in" in low or "taymer" in low or "напомни" in low or "eslat" in low:
            return "Timer set! [REMINDER: 10 | Test reminder]"
        if "shopping list" in low and "add" in low:
            item = message[low.find("add") + len("add"):low.find("to my shopping list")].strip() or "item"
            return f"Added! [LIST_ADD: shopping | {item}]"
        if "remove" in low and "shopping list" in low:
            item = message[low.find("remove") + len("remove"):low.find("from my shopping list")].strip() or "item"
            return f"Removed! [LIST_REMOVE: shopping | {item}]"
        if "what is on my shopping list" in low or "what's on my shopping list" in low:
            lists = get_all_lists(self.telegram_user_id)
            items = lists.get("shopping", [])
            return "Your shopping list: " + (", ".join(items) if items else "(empty)")
        if "i spent" in low:
            return "Logged! [EXPENSE: 25 | USD | food | lunch]"
        if "i ate" in low:
            return "Logged! [LOG_FOOD: test meal | 500]"
        if "birthday is" in low:
            return "Saved! [ADD_EVENT: Test Person | 1990-05-15 | birthday]"
        if "export" in low:
            return "Preparing your data export! [EXPORT_DATA]"
        if "my name is" in low:
            name = message[low.find("my name is") + len("my name is"):].strip()
            memory.append({"type": "name", "value": name})
            return f"I will remember that your name is {name}."
        if "what is my name" in low:
            names = [m["value"] for m in memory if m.get("type") == "name"]
            if names:
                return f"Your name is {names[-1]}."
            return "I don't know your name yet."
        return f"Echo ({current_lang}): {message}"
