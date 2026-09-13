"""
Hermes adapter — bridges each Telegram user to an isolated
OpenAI-backed profile with persistent memory, conversation history,
and real timer / reminder scheduling.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta

from app.reminders.manager import add_reminder, get_pending_reminders, cancel_reminder

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
        name = {"ru": "Лейла", "en": "Leyla", "uz": "Laylo"}.get(current_lang, "Leyla")

        now_utc = datetime.now(timezone.utc)
        now_uz = now_utc.astimezone(timezone(timedelta(hours=5)))
        now_ru = now_utc.astimezone(timezone(timedelta(hours=3)))

        prompt = (
            f"You are {name}, a highly capable, warm, and thoughtful personal AI assistant.\n"
            f"Your name is {name}.\n"
            "Imagine yourself as a helpful companion with a computer who assists the user with everyday "
            "tasks, calculations, expenses, researching, writing, and organizing their day.\n\n"
            f"CURRENT TIME & DATE:\n"
            f"• UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Uzbekistan (UTC+5): {now_uz.strftime('%Y-%m-%d %H:%M:%S (%A)')}\n"
            f"• Moscow (UTC+3): {now_ru.strftime('%Y-%m-%d %H:%M:%S (%A)')}\n\n"
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
            "• When the user asks for a timer or reminder (e.g. 'Remind me in 10 minutes to take medicine', "
            "'5 minutga taymer qo\\'y', 'Ertaga soat 08:00 da dori ichishni eslat', 'Напомни через полчаса выключить суп'):\n"
            "  1. Calculate the exact delay in seconds from the current time.\n"
            "     - 1 minute = 60\n"
            "     - 5 minutes = 300\n"
            "     - 10 minutes = 600\n"
            "     - 1 hour = 3600\n"
            "     - Specific time (e.g. at 18:00): calculate difference in seconds between the current local time and the target time.\n"
            "  2. Append the tag `[REMINDER: <seconds> | <text to remind>]` at the end of your response.\n"
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
