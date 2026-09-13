import unittest
import os
import sqlite3
import tempfile
import shutil

# Set test environment
temp_dir = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "test.db")
os.environ["DATA_DIR"] = temp_dir
os.environ["TELEGRAM_BOT_TOKEN"] = "test"

from app.database.db import init_db
from app.users.manager import get_or_create_user, update_user_language, get_user
from app.hermes.adapter import HermesAdapter

class TestIsolationAndMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(temp_dir)
        
    def test_user_creation_and_language(self):
        user, is_new = get_or_create_user(123)
        self.assertTrue(is_new)
        self.assertEqual(user["telegram_user_id"], 123)
        self.assertEqual(user["hermes_profile"], "telegram_123")
        
        update_user_language(123, "ru")
        user = get_user(123)
        self.assertEqual(user["language"], "ru")
        
    def test_memory_isolation(self):
        # Create user A
        user_a, _ = get_or_create_user(111)
        adapter_a = HermesAdapter(user_a["hermes_profile"])
        
        # Create user B
        user_b, _ = get_or_create_user(222)
        adapter_b = HermesAdapter(user_b["hermes_profile"])
        
        # User A says their name
        adapter_a.send_message("My name is Alice", "en")
        
        # User B says their name
        adapter_b.send_message("My name is Bob", "en")
        
        # User A asks for their name
        resp_a = adapter_a.send_message("What is my name?", "en")
        self.assertIn("Alice", resp_a)
        self.assertNotIn("Bob", resp_a)
        
        # User B asks for their name
        resp_b = adapter_b.send_message("What is my name?", "en")
        self.assertIn("Bob", resp_b)
        self.assertNotIn("Alice", resp_b)

    def test_natural_language_switching(self):
        user, _ = get_or_create_user(333)
        adapter = HermesAdapter(user["hermes_profile"])
        
        # Send english switch
        response = adapter.send_message("I want to speak english", "ru")
        self.assertIn("[LANGUAGE_CHANGED_TO: en]", response)
        
        # Send generic phrase
        response2 = adapter.send_message("Hello", "en")
        self.assertNotIn("[LANGUAGE_CHANGED_TO", response2)

if __name__ == '__main__':
    unittest.main()
