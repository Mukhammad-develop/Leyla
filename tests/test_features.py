"""
Tests — new feature managers and adapter tag processing.

Structured lists/expenses/calories/events, usage limits, and the
memory-loss fix (data survives history truncation because it lives in
dedicated tables). Runs against the mock LLM, no network calls.
"""

import os
import shutil
import tempfile
import unittest

# ---------- isolated test environment BEFORE any app imports ----------
_temp_dir = tempfile.mkdtemp(prefix="leyla_feat_test_")
os.environ["DATABASE_PATH"] = os.path.join(_temp_dir, "test.db")
os.environ["DATA_DIR"] = _temp_dir
os.environ["TELEGRAM_BOT_TOKEN"] = "test"
os.environ["TEST_MODE"] = "1"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("OPENROUTER_API_KEY", None)

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.db import init_db  # noqa: E402
from app.hermes.adapter import HermesAdapter  # noqa: E402
from app.imagegen.generator import _bytes_from_image_value  # noqa: E402
from app.telegram.formatting import format_telegram_html, strip_telegram_markdown  # noqa: E402
from app.users.manager import (  # noqa: E402
    get_or_create_user,
    get_user,
    update_voice_mode,
    update_briefing_enabled,
    update_briefing_time,
    update_calorie_goal,
    update_user_city,
)
from app.lists.manager import add_item, remove_item, clear_list, get_all_lists  # noqa: E402
from app.expenses.manager import add_expense, get_recent_expenses, get_month_totals  # noqa: E402
from app.calories.manager import log_food, get_day_total, get_all_entries  # noqa: E402
from app.events.manager import (  # noqa: E402
    add_event,
    delete_event,
    get_all_events,
    get_events_on,
    mark_wished,
    get_upcoming_events,
)
from app.usage.tracker import (  # noqa: E402
    increment_usage,
    get_usage_today,
    is_limit_reached,
    get_daily_limit,
    set_daily_limit,
)


class TestListsManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_add_and_get(self):
        add_item(700001, "shopping", "milk")
        add_item(700001, "shopping", "eggs")
        add_item(700001, "todo", "call mom")
        lists = get_all_lists(700001)
        self.assertEqual(lists["shopping"], ["milk", "eggs"])
        self.assertEqual(lists["todo"], ["call mom"])

    def test_isolation_between_users(self):
        add_item(700002, "shopping", "bread")
        self.assertEqual(get_all_lists(700003), {})
        self.assertEqual(get_all_lists(700002)["shopping"], ["bread"])

    def test_remove_item(self):
        add_item(700004, "shopping", "milk")
        add_item(700004, "shopping", "eggs")
        self.assertTrue(remove_item(700004, "shopping", "milk"))
        self.assertEqual(get_all_lists(700004)["shopping"], ["eggs"])

    def test_clear_list(self):
        add_item(700005, "shopping", "milk")
        clear_list(700005, "shopping")
        self.assertEqual(get_all_lists(700005), {})


class TestExpensesManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_add_and_recent(self):
        add_expense(710001, 50000, "uzs", "food", "lunch", "2026-09-19")
        add_expense(710001, 20, "usd", "transport", "taxi", "2026-09-20")
        recent = get_recent_expenses(710001)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["note"], "taxi")  # newest first

    def test_month_totals(self):
        add_expense(710002, 100, "USD", "food", "a", "2026-09-01")
        add_expense(710002, 50, "USD", "food", "b", "2026-09-15")
        add_expense(710002, 999, "USD", "food", "c", "2026-08-31")  # other month
        totals = get_month_totals(710002, "2026-09")
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["total"], 150)


class TestCaloriesManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_log_and_total(self):
        log_food(720001, "plov", 600, "2026-09-20")
        log_food(720001, "apple", 95, "2026-09-20")
        log_food(720001, "cake", 450, "2026-09-19")  # other day
        self.assertEqual(get_day_total(720001, "2026-09-20"), 695)
        self.assertEqual(len(get_all_entries(720001)), 3)


class TestEventsManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_add_get_delete(self):
        add_event(730001, "Mom", "1990-03-15", "birthday")
        events = get_all_events(730001)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_date"], "1990-03-15")
        self.assertTrue(delete_event(730001, "Mom"))
        self.assertEqual(get_all_events(730001), [])

    def test_events_on_month_day(self):
        add_event(730002, "Ali", "1995-10-05", "birthday")
        self.assertEqual(len(get_events_on(730002, "10-05")), 1)
        self.assertEqual(get_events_on(730002, "10-06"), [])

    def test_mark_wished(self):
        add_event(730003, "Bob", "2000-01-01", "birthday")
        ev = get_all_events(730003)[0]
        mark_wished(ev["id"], 2026)
        self.assertEqual(get_all_events(730003)[0]["last_wished_year"], 2026)

    def test_upcoming(self):
        import datetime
        add_event(730004, "Soon", "1990-06-25", "birthday")
        add_event(730005, "Later", "1990-12-01", "birthday")
        upcoming = get_upcoming_events(730004, datetime.date(2026, 6, 20), days=40)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0]["in_days"], 5)


class TestUsageTracker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_increment_and_count(self):
        before = get_usage_today(740001)
        increment_usage(740001)
        increment_usage(740001)
        self.assertEqual(get_usage_today(740001), before + 2)

    def _clear_limit_setting(self):
        from app.database.db import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'daily_message_limit'")
            conn.commit()

    def test_limit_via_admin_setting(self):
        set_daily_limit(3)
        try:
            for _ in range(3):
                increment_usage(740002)
            self.assertTrue(is_limit_reached(740002))
            self.assertFalse(is_limit_reached(740003))
        finally:
            set_daily_limit(0)

    def test_env_fallback_limit(self):
        self._clear_limit_setting()
        os.environ["DAILY_MESSAGE_LIMIT"] = "3"
        try:
            for _ in range(3):
                increment_usage(740006)
            self.assertTrue(is_limit_reached(740006))
        finally:
            os.environ["DAILY_MESSAGE_LIMIT"] = "0"

    def test_default_unlimited(self):
        os.environ.pop("DAILY_MESSAGE_LIMIT", None)
        self.assertEqual(get_daily_limit(), 0)
        for _ in range(500):
            increment_usage(740004)
        self.assertFalse(is_limit_reached(740004))

    def test_admin_setting_overrides_env(self):
        os.environ["DAILY_MESSAGE_LIMIT"] = "0"
        try:
            set_daily_limit(5)
            self.assertEqual(get_daily_limit(), 5)
            for _ in range(5):
                increment_usage(740005)
            self.assertTrue(is_limit_reached(740005))
        finally:
            set_daily_limit(0)
        self.assertEqual(get_daily_limit(), 0)


class TestUserSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_voice_mode(self):
        get_or_create_user(750001)
        update_voice_mode(750001, "both")
        self.assertEqual(get_user(750001)["voice_mode"], "both")
        update_voice_mode(750001, "invalid")  # ignored
        self.assertEqual(get_user(750001)["voice_mode"], "both")

    def test_briefing(self):
        get_or_create_user(750002)
        update_briefing_enabled(750002, False)
        update_briefing_time(750002, "07:30")
        user = get_user(750002)
        self.assertEqual(user["briefing_enabled"], 0)
        self.assertEqual(user["briefing_time"], "07:30")

    def test_city_and_calorie_goal(self):
        get_or_create_user(750003)
        update_user_city(750003, "Tashkent")
        update_calorie_goal(750003, 2000)
        user = get_user(750003)
        self.assertEqual(user["city"], "Tashkent")
        self.assertEqual(user["calorie_goal"], 2000)


class TestTelegramFormatting(unittest.TestCase):
    def test_bold_markdown_becomes_telegram_html(self):
        self.assertEqual(format_telegram_html("**text**"), "<b>text</b>")
        self.assertEqual(
            format_telegram_html("🕌 **Prayer Times — Tashkent**\nFajr: **05:00**"),
            "🕌 <b>Prayer Times — Tashkent</b>\nFajr: <b>05:00</b>",
        )

    def test_html_escapes_user_content(self):
        self.assertEqual(
            format_telegram_html("**total** < 5 & rising"),
            "<b>total</b> &lt; 5 &amp; rising",
        )

    def test_plain_fallback_never_leaves_double_asterisks(self):
        self.assertEqual(strip_telegram_markdown("**text** and `code`"), "text and code")
        self.assertEqual(
            strip_telegram_markdown("# Title\n**Section**\n[link](https://example.com)"),
            "Title\nSection\nlink (https://example.com)",
        )


class TestImageResponseParsing(unittest.TestCase):
    def test_data_url_and_raw_base64_decode(self):
        import base64
        payload = base64.b64encode(b"fake-image").decode()
        self.assertEqual(_bytes_from_image_value(f"data:image/png;base64,{payload}"), b"fake-image")
        self.assertEqual(_bytes_from_image_value(payload), b"fake-image")


class TestAdapterTagProcessing(unittest.TestCase):
    """The memory-loss fix: structured data lands in tables, not just history."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def _make_user(self, uid):
        user, _ = get_or_create_user(uid)
        return HermesAdapter(user["hermes_profile"], telegram_user_id=uid, chat_id=uid)

    def test_shopping_list_survives_history_truncation(self):
        adapter = self._make_user(760001)
        adapter.send_message("add milk to my shopping list", "en")
        adapter.send_message("add eggs to my shopping list", "en")

        # Simulate months passing: wipe conversation history entirely
        adapter._write_json(adapter.history_file, [])

        # A brand-new adapter (fresh "session") still knows the list,
        # because items live in the DB, not in the chat history.
        fresh = self._make_user(760001)
        resp = fresh.send_message("What is on my shopping list?", "en")
        self.assertIn("milk", resp)
        self.assertIn("eggs", resp)

        # System prompt carries the list too (what the real LLM would see)
        prompt = fresh._get_system_prompt("en")
        self.assertIn("shopping", prompt)
        self.assertIn("milk", prompt)

    def test_list_remove_via_message(self):
        adapter = self._make_user(760002)
        adapter.send_message("add milk to my shopping list", "en")
        adapter.send_message("remove milk from my shopping list", "en")
        self.assertEqual(get_all_lists(760002).get("shopping", []), [])

    def test_expense_logged_via_message(self):
        adapter = self._make_user(760003)
        adapter.send_message("I spent 25 USD on lunch", "en")
        recent = get_recent_expenses(760003)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["amount"], 25.0)
        self.assertEqual(recent[0]["currency"], "USD")

    def test_food_logged_via_message(self):
        adapter = self._make_user(760004)
        adapter.send_message("I ate a big lunch", "en")
        today = adapter._user_local_today()
        self.assertEqual(get_day_total(760004, today), 500)

    def test_event_added_via_message(self):
        adapter = self._make_user(760005)
        adapter.send_message("My friend's birthday is May 15", "en")
        events = get_all_events(760005)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_date"], "1990-05-15")

    def test_city_saved_via_message(self):
        adapter = self._make_user(760006)
        adapter.send_message("Men Toshkentdaman", "uz")
        self.assertEqual(get_user(760006)["city"], "Tashkent")

    def test_export_requested_via_message(self):
        adapter = self._make_user(760007)
        adapter.send_message("Export my data please", "en")
        self.assertTrue(getattr(adapter, "_pending_export", False))

    def test_image_request_without_tag_still_sets_pending_prompt(self):
        adapter = self._make_user(760008)
        adapter.send_message("Draw a 4 teachers teaching politics", "en")
        self.assertEqual(
            getattr(adapter, "_pending_image_prompt", None),
            "Draw a 4 teachers teaching politics",
        )


def tearDownModule():
    shutil.rmtree(_temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
