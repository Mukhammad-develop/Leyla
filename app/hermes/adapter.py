"""
Hermes adapter — bridges each Telegram user to an isolated
OpenAI-backed profile with persistent memory, conversation history,
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
from app.users.manager import get_user, update_user_timezone

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
# Singleton OpenAI client
# ---------------------------------------------------------------------------
_openai_client = None
_openai_client_lock = threading.Lock()


def _get_openai_client():
    """Return a shared OpenAI client or None when unavailable."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    with _openai_client_lock:
        if _openai_client is not None:
            return _openai_client
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — LLM calls disabled")
            return None
        try:
            import openai
            _openai_client = openai.OpenAI(api_key=api_key)
            return _openai_client
        except Exception as exc:
            logger.error("Failed to initialise OpenAI client: %s", exc)
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
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")

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
                f"calculate the exact seconds difference from their local time ({user_local_now.strftime('%H:%M')}).\n"
                f"• If the user mentions moving or changing their city/country (e.g. 'I am in Dubai now' or 'Men Toshkentdaman'), "
                f"update their timezone by appending `[SET_TIMEZONE: <IANA_or_offset>]`.\n"
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
                "       - Calculate the delay from their local time and append `[REMINDER: <seconds> | <text>]`.\n"
                "       - Warmly confirm their city/timezone has been saved and the reminder is set!\n"
            )

        prompt = (
            f"You are {name}, a highly capable, warm, and thoughtful personal AI assistant.\n"
            f"Your name is {name}.\n"
            "Imagine yourself as a helpful companion with a computer who assists the user with everyday "
            "tasks, calculations, expenses, researching, writing, and organizing their day.\n\n"
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

        return prompt

    # ---- Public entry point -----------------------------------------------

    def send_message(self, message: str, current_lang: str) -> str:
        """Thread-safe: acquire per-user lock → call LLM → persist state."""
        lock = _get_user_lock(self.profile_id)
        with lock:
            history = self._read_json(self.history_file)
            memory = self._read_json(self.memory_file)

            client = _get_openai_client()
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
            logger.error("OpenAI API error for %s: %s", self.profile_id, exc)
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
            return "Toshkent vaqti saqlandi! [SET_TIMEZONE: Asia/Tashkent]"
        if "remind me in" in low or "taymer" in low or "напомни" in low or "eslat" in low:
            return "Timer set! [REMINDER: 10 | Test reminder]"
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
