"""
Tests — user creation, profile isolation, memory isolation, language switching.

All tests run against a mock LLM (TEST_MODE=1) so no network calls are made.
"""

import os
import shutil
import tempfile
import unittest

# ---------- set up isolated test environment BEFORE any app imports ----------
_temp_dir = tempfile.mkdtemp(prefix="leyla_test_")
os.environ["DATABASE_PATH"] = os.path.join(_temp_dir, "test.db")
os.environ["DATA_DIR"] = _temp_dir
os.environ["TELEGRAM_BOT_TOKEN"] = "test"
os.environ["TEST_MODE"] = "1"
# Remove API key so the adapter uses mock mode even if set globally
os.environ.pop("OPENAI_API_KEY", None)

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.db import init_db  # noqa: E402
from app.hermes.adapter import HermesAdapter  # noqa: E402
from app.users.manager import (  # noqa: E402
    get_or_create_user,
    get_user,
    update_user_language,
)


class TestUserCreation(unittest.TestCase):
    """Basic user CRUD."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_create_new_user(self):
        user, is_new = get_or_create_user(100001)
        self.assertTrue(is_new)
        self.assertEqual(user["telegram_user_id"], 100001)
        self.assertEqual(user["hermes_profile"], "telegram_100001")
        self.assertEqual(user["language"], "")

    def test_existing_user_not_recreated(self):
        get_or_create_user(100002)
        user, is_new = get_or_create_user(100002)
        self.assertFalse(is_new)

    def test_language_update(self):
        get_or_create_user(100003)
        update_user_language(100003, "ru")
        user = get_user(100003)
        self.assertEqual(user["language"], "ru")

    def test_language_update_overwrites(self):
        get_or_create_user(100004)
        update_user_language(100004, "en")
        update_user_language(100004, "uz")
        user = get_user(100004)
        self.assertEqual(user["language"], "uz")

    def test_nonexistent_user_returns_none(self):
        self.assertIsNone(get_user(999999))


class TestMemoryIsolation(unittest.TestCase):
    """User A's memory must never leak into User B's session."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_isolated_names(self):
        user_a, _ = get_or_create_user(200001)
        user_b, _ = get_or_create_user(200002)
        adapter_a = HermesAdapter(user_a["hermes_profile"])
        adapter_b = HermesAdapter(user_b["hermes_profile"])

        adapter_a.send_message("My name is Alice", "en")
        adapter_b.send_message("My name is Bob", "en")

        resp_a = adapter_a.send_message("What is my name?", "en")
        self.assertIn("Alice", resp_a)
        self.assertNotIn("Bob", resp_a)

        resp_b = adapter_b.send_message("What is my name?", "en")
        self.assertIn("Bob", resp_b)
        self.assertNotIn("Alice", resp_b)

    def test_separate_profile_directories(self):
        user_a, _ = get_or_create_user(200003)
        user_b, _ = get_or_create_user(200004)
        a = HermesAdapter(user_a["hermes_profile"])
        b = HermesAdapter(user_b["hermes_profile"])
        self.assertNotEqual(a.profile_dir, b.profile_dir)
        self.assertTrue(os.path.isdir(a.profile_dir))
        self.assertTrue(os.path.isdir(b.profile_dir))


class TestLanguageSwitching(unittest.TestCase):
    """Natural language switching via LLM tags."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_switch_to_english(self):
        user, _ = get_or_create_user(300001)
        adapter = HermesAdapter(user["hermes_profile"])
        resp = adapter.send_message("I want to speak english", "ru")
        self.assertIn("[LANGUAGE_CHANGED_TO: en]", resp)

    def test_switch_to_russian(self):
        user, _ = get_or_create_user(300002)
        adapter = HermesAdapter(user["hermes_profile"])
        resp = adapter.send_message("Давай по-русски", "en")
        self.assertIn("[LANGUAGE_CHANGED_TO: ru]", resp)

    def test_switch_to_uzbek(self):
        user, _ = get_or_create_user(300003)
        adapter = HermesAdapter(user["hermes_profile"])
        resp = adapter.send_message("O'zbekcha gaplash", "en")
        self.assertIn("[LANGUAGE_CHANGED_TO: uz]", resp)

    def test_no_switch_on_normal_message(self):
        user, _ = get_or_create_user(300004)
        adapter = HermesAdapter(user["hermes_profile"])
        resp = adapter.send_message("Hello, how are you?", "en")
        self.assertNotIn("[LANGUAGE_CHANGED_TO", resp)


class TestHistoryPersistence(unittest.TestCase):
    """Conversation history survives adapter re-creation."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_history_file_created(self):
        user, _ = get_or_create_user(400001)
        adapter = HermesAdapter(user["hermes_profile"])
        adapter.send_message("Hello", "en")
        self.assertTrue(os.path.exists(adapter.history_file))

    def test_memory_survives_new_adapter(self):
        user, _ = get_or_create_user(400002)
        adapter1 = HermesAdapter(user["hermes_profile"])
        adapter1.send_message("My name is Charlie", "en")

        # Simulate restart: new adapter instance, same profile
        adapter2 = HermesAdapter(user["hermes_profile"])
        resp = adapter2.send_message("What is my name?", "en")
        self.assertIn("Charlie", resp)


class TestConcurrency(unittest.TestCase):
    """Basic thread-safety smoke test."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_concurrent_messages_different_users(self):
        import threading

        errors = []

        def worker(uid, name):
            try:
                user, _ = get_or_create_user(uid)
                adapter = HermesAdapter(user["hermes_profile"])
                adapter.send_message(f"My name is {name}", "en")
                resp = adapter.send_message("What is my name?", "en")
                if name not in resp:
                    errors.append(f"User {uid}: expected {name}, got: {resp}")
            except Exception as e:
                errors.append(f"User {uid}: exception: {e}")

        threads = [
            threading.Thread(target=worker, args=(500001 + i, f"User{i}"))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrency errors: {errors}")


class TestReminders(unittest.TestCase):
    """Timer and reminder creation and retrieval."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_reminder_created_via_message(self):
        from app.reminders.manager import get_pending_reminders, add_reminder, cancel_reminder
        user, _ = get_or_create_user(600001)
        adapter = HermesAdapter(user["hermes_profile"], telegram_user_id=600001, chat_id=600001)
        resp = adapter.send_message("Remind me in 10 minutes to test", "en")
        reminders = get_pending_reminders(600001)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["text"], "Test reminder")
        self.assertEqual(reminders[0]["status"], "pending")

        # Test cancellation
        cancelled = cancel_reminder(600001, reminders[0]["id"])
        self.assertTrue(cancelled)
        reminders_after = get_pending_reminders(600001)
        self.assertEqual(len(reminders_after), 0)

    def test_timezone_saved_via_message(self):
        user, _ = get_or_create_user(600002)
        adapter = HermesAdapter(user["hermes_profile"], telegram_user_id=600002, chat_id=600002)
        resp = adapter.send_message("Men Toshkentdaman", "uz")
        updated_user = get_user(600002)
        self.assertEqual(updated_user["timezone"], "Asia/Tashkent")


def tearDownModule():
    shutil.rmtree(_temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
