import os
import logging
import json
import threading
import re
try:
    import openai
except ImportError:
    openai = None

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
        
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
        self.test_mode = os.environ.get("TEST_MODE") == "1"
        
        if self.api_key and openai:
            self.client = openai.OpenAI(api_key=self.api_key)
        else:
            self.client = None
            if not self.test_mode:
                logger.warning("OpenAI API key not set or openai package not installed!")

    def _get_system_prompt(self, current_lang):
        return (
            "You are Hermes, a highly capable personal AI assistant.\n"
            "Imagine yourself as a person who has access to a computer and can help the user with everyday calculations, "
            "tracking expenses, researching, writing, and organizing.\n"
            f"The user's current preferred language code is: '{current_lang}'. "
            "Please respond primarily in this language.\n\n"
            "IMPORTANT RULES ABOUT LANGUAGE SWITCHING:\n"
            "If the user EXPLICITLY requests to change the conversation language (e.g. 'Speak English', 'Давай по-русски', 'Endi o\\'zbekcha gaplashamiz'), "
            "you MUST acknowledge the change in the NEW language and include EXACTLY the string '[LANGUAGE_CHANGED_TO: <CODE>]' at the very end of your response, "
            "where <CODE> is 'ru', 'en', or 'uz'. Do NOT include this tag unless they explicitly ask to change the language settings.\n\n"
            "MEMORY INSTRUCTIONS:\n"
            "You can remember information about the user. If they tell you something to remember, acknowledge it and include '[REMEMBER: <fact>]' in your response. "
            "Use the provided memory context to personalize your answers."
        )

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
            
            if self.test_mode or not self.client:
                response = self._mock_llm_response(message, current_lang, memory)
            else:
                response = self._openai_llm_response(message, current_lang, history, memory)
                
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": response})
            
            self._write_json(self.history_file, history[-50:]) # keep last 50
            self._write_json(self.memory_file, memory)
            
            return response
            
    def _openai_llm_response(self, message, current_lang, history, memory):
        system_prompt = self._get_system_prompt(current_lang)
        if memory:
            system_prompt += "\n\nUser Memory Context:\n" + "\n".join(f"- {m['value']}" for m in memory if 'value' in m)

        messages = [{"role": "system", "content": system_prompt}]
        
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        messages.append({"role": "user", "content": message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7
            )
            reply = response.choices[0].message.content
            
            # Extract memory tags
            memory_matches = re.findall(r'\[REMEMBER:\s*(.+?)\]', reply)
            for fact in memory_matches:
                memory.append({"type": "fact", "value": fact})
                reply = reply.replace(f"[REMEMBER: {fact}]", "").strip()
                
            return reply
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "I'm having trouble connecting to my brain right now. Please try again later."

    def _mock_llm_response(self, message, current_lang, memory):
        msg_lower = message.lower()
        if "speak english" in msg_lower or "english" in msg_lower and "speak" in msg_lower:
            return "Okay, I will speak English from now on. [LANGUAGE_CHANGED_TO: en]"
        elif "по-русски" in msg_lower or "русский" in msg_lower:
            return "Хорошо, теперь я буду говорить по-русски. [LANGUAGE_CHANGED_TO: ru]"
        elif "o'zbekcha" in msg_lower or "o'zbek" in msg_lower:
            return "Yaxshi, endi men o'zbek tilida gaplashaman. [LANGUAGE_CHANGED_TO: uz]"
        
        if "my name is" in msg_lower:
            orig_name = message[message.lower().find("my name is") + len("my name is"):].strip()
            memory.append({"type": "name", "value": orig_name})
            return f"I will remember that your name is {orig_name}."
            
        if "what is my name" in msg_lower:
            names = [m["value"] for m in memory if m["type"] == "name"]
            if names:
                return f"Your name is {names[-1]}."
            return "I don't know your name yet."
            
        return f"Echo ({current_lang}): {message}"
