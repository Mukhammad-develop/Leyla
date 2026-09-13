import os
import logging
import json
import threading

logger = logging.getLogger(__name__)

# Global lock for Hermes concurrency safety per user
_user_locks = {}
_lock_mutex = threading.Lock()

def _get_user_lock(profile_id: str):
    with _lock_mutex:
        if profile_id not in _user_locks:
            _user_locks[profile_id] = threading.Lock()
        return _user_locks[profile_id]

class HermesAdapter:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        # We simulate the Hermes environment by ensuring a directory per profile
        self.profile_dir = os.path.join(os.environ.get("DATA_DIR", "data"), "hermes_profiles", profile_id)
        os.makedirs(self.profile_dir, exist_ok=True)
        self.memory_file = os.path.join(self.profile_dir, "memory.json")
        self.history_file = os.path.join(self.profile_dir, "history.json")
        self.system_prompt = self._load_system_prompt()
        
    def _load_system_prompt(self):
        return "System prompt"

    def _read_json(self, path):
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def send_message(self, message: str, current_lang: str) -> str:
        """Sends a message to the Hermes profile and returns the response."""
        lock = _get_user_lock(self.profile_id)
        with lock:
            history = self._read_json(self.history_file)
            memory = self._read_json(self.memory_file)
            
            history.append({"role": "user", "content": message})
            
            response = self._mock_llm_response(message, current_lang, memory)
            history.append({"role": "assistant", "content": response})
            
            self._write_json(self.history_file, history)
            self._write_json(self.memory_file, memory)
            
            return response
            
    def _mock_llm_response(self, message, current_lang, memory):
        msg_lower = message.lower()
        if "speak english" in msg_lower or "english" in msg_lower and "speak" in msg_lower:
            return "Okay, I will speak English from now on. [LANGUAGE_CHANGED_TO: en]"
        elif "по-русски" in msg_lower or "русский" in msg_lower:
            return "Хорошо, теперь я буду говорить по-русски. [LANGUAGE_CHANGED_TO: ru]"
        elif "o'zbekcha" in msg_lower or "o'zbek" in msg_lower:
            return "Yaxshi, endi men o'zbek tilida gaplashaman. [LANGUAGE_CHANGED_TO: uz]"
        
        if "my name is" in msg_lower:
            name = message.lower().split("my name is")[-1].strip()
            # to keep original casing, let's extract it from original message
            orig_name = message[message.lower().find("my name is") + len("my name is"):].strip()
            memory.append({"type": "name", "value": orig_name})
            return f"I will remember that your name is {orig_name}."
            
        if "what is my name" in msg_lower:
            names = [m["value"] for m in memory if m["type"] == "name"]
            if names:
                return f"Your name is {names[-1]}."
            return "I don't know your name yet."
            
        return f"Echo ({current_lang}): {message}"
