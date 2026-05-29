import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class EnvLoadingTests(unittest.TestCase):
    def test_load_env_file_sets_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "LLM_PROVIDER=anthropic",
                        "OPENAI_API_KEY='sk-test-value'",
                        "# ignored comment",
                        "EMPTY_VALUE=",
                    ]
                ),
                encoding="utf-8",
            )

            old_provider = os.environ.pop("LLM_PROVIDER", None)
            old_key = os.environ.pop("OPENAI_API_KEY", None)
            old_empty = os.environ.pop("EMPTY_VALUE", None)
            try:
                app.load_dotenv_file(env_path)
                self.assertEqual(os.environ["LLM_PROVIDER"], "anthropic")
                self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test-value")
                self.assertEqual(os.environ["EMPTY_VALUE"], "")
            finally:
                for key, value in {
                    "LLM_PROVIDER": old_provider,
                    "OPENAI_API_KEY": old_key,
                    "EMPTY_VALUE": old_empty,
                }.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_load_env_file_does_not_overwrite_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OPENAI_API_KEY=from-file\n", encoding="utf-8")

            old_key = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "from-shell"
            try:
                app.load_dotenv_file(env_path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-shell")
            finally:
                if old_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_key


class DatabasePathTests(unittest.TestCase):
    def test_database_path_can_be_configured_with_parent_directory_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "events.db"
            with patch.dict(os.environ, {"ICS_GEN_DB_PATH": str(db_path)}):
                app.init_db()

            self.assertTrue(db_path.is_file())


class CalendarFeedTests(unittest.TestCase):
    def test_default_feed_path_is_public_when_token_is_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.calendar_feed_path(), "/calendar.ics")
            self.assertEqual(app.calendar_feed().status_code, 200)
            self.assertEqual(app.token_calendar_feed("anything").status_code, 404)

    def test_tokenized_feed_path_disables_plain_feed(self):
        token = "test-token"
        with patch.dict(os.environ, {"CALENDAR_FEED_TOKEN": token}, clear=True):
            self.assertEqual(app.calendar_feed_path(), f"/calendar/{token}.ics")
            self.assertEqual(app.calendar_feed().status_code, 404)
            self.assertEqual(app.token_calendar_feed(token).status_code, 200)
            self.assertEqual(app.token_calendar_feed("wrong").status_code, 404)

    def test_index_template_uses_configured_feed_path(self):
        token = "test-token"
        feed_path = f"/calendar/{token}.ics"

        html = app.templates.get_template("index.html").render(
            request={},
            events=[],
            error=None,
            provider="openai",
            calendar_feed_path=feed_path,
        )

        self.assertIn(feed_path, html)
        self.assertNotIn('href="/calendar.ics"', html)
        self.assertIn("window.location.origin + calendarFeedPath", html)


class IconAssetTests(unittest.TestCase):
    def test_all_declared_icon_files_exist_and_are_routable(self):
        expected_icons = {
            "favicon.ico",
            "favicon.png",
            "apple-touch-icon.png",
            "apple-touch-icon-precomposed.png",
        }

        self.assertEqual(app.ICON_FILE_NAMES, expected_icons)
        for icon_name in expected_icons:
            with self.subTest(icon_name=icon_name):
                self.assertTrue((app.STATIC_DIR / icon_name).is_file())
                self.assertEqual(app.static_icon_response(icon_name).status_code, 200)


class PromptTests(unittest.TestCase):
    def test_prompt_instructs_reasonable_default_alerts(self):
        prompt = app.build_prompt("Dentist tomorrow at 9am")

        self.assertIn("empty alerts array", prompt)
        self.assertIn("deterministic defaults", prompt)
        self.assertIn("explicit alert offsets", prompt)


if __name__ == "__main__":
    unittest.main()
