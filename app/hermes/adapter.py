"""
Hermes adapter — bridges each Telegram user to an isolated
OpenAI-backed profile with persistent memory and conversation history.

In production the OpenAI client is reused (singleton per profile_id).
A per-profile threading lock serializes state mutations so concurrent
Telegram updates for the same user never corrupt files.

File writes use atomic rename to prevent corruption on crash.
"""

import json
import logging
import os
import re
import tempfile
import threading

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
# Singleton OpenAI client  (created once, reused for every message)
# ---------------------------------------------------------------------------
_openai_client = None
_openai_client_lock = threading.Lock()


def _get_openai_client():
    """Return a shared OpenAI client or None when unavailable."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    with _openai_client_lock:
        if _openai_client is not None:          # double-check
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
    """One adapter instance per incoming message.  Cheap to construct."""

    def __init__(self, profile_id: str):
        self.profile_id = profile_id
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
            os.replace(tmp_path, path)       # atomic on POSIX
        except BaseException:
            # Clean up the temp file if something goes wrong
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ---- System prompt ----------------------------------------------------

    @staticmethod
    def _get_system_prompt(current_lang: str) -> str:
        return (
            "You are Leyla, a highly capable personal AI assistant.\n"
            "Imagine yourself as a person who has access to a computer and can "
            "help the user with everyday calculations, tracking expenses, "
            "researching, writing, and organizing.\n\n"
            f"The user's current preferred language code is: '{current_lang}'.\n"
            "You MUST respond in that language unless the user explicitly asks "
            "you to switch.\n\n"
            "LANGUAGE SWITCHING RULES:\n"
            "• If the user EXPLICITLY asks to change the conversation language "
            "(e.g. 'Speak English', 'Давай по-русски', "
            "'Endi o\\'zbekcha gaplashamiz'), acknowledge the change in the "
            "NEW language and append EXACTLY the tag "
            "'[LANGUAGE_CHANGED_TO: <CODE>]' at the very end of your "
            "response, where <CODE> is one of: ru, en, uz.\n"
            "• Do NOT emit this tag for any other reason.\n\n"
            "MEMORY RULES:\n"
            "• When the user tells you a fact to remember, store it by "
            "appending '[REMEMBER: <fact>]' to your response.\n"
            "• Use the memory context below to personalise answers.\n"
        )

    # ---- Public entry point -----------------------------------------------

    def send_message(self, message: str, current_lang: str) -> str:
        """Thread-safe: acquire per-user lock → call LLM → persist state."""
        lock = _get_user_lock(self.profile_id)
        with lock:
            history = self._read_json(self.history_file)
            memory = self._read_json(self.memory_file)

            client = _get_openai_client()
            if client is None:
                # Fallback for tests / missing key
                response = self._mock_llm_response(message, current_lang, memory)
            else:
                response = self._openai_response(
                    client, message, current_lang, history, memory,
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
                temperature=0.7,
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

    @staticmethod
    def _mock_llm_response(message, current_lang, memory):
        low = message.lower()
        if "speak english" in low:
            return "Okay, I will speak English now. [LANGUAGE_CHANGED_TO: en]"
        if "по-русски" in low or "русский" in low:
            return "Хорошо, перехожу на русский. [LANGUAGE_CHANGED_TO: ru]"
        if "o'zbekcha" in low or "o'zbek" in low:
            return "Xo'p, endi o'zbekchada gaplashaman. [LANGUAGE_CHANGED_TO: uz]"
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
