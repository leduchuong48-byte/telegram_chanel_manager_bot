from __future__ import annotations

import unittest

from app.core.runtime_settings import load_runtime_settings


class RuntimeSettingsTest(unittest.TestCase):
    def test_runtime_settings_prefers_config_json_values(self) -> None:
        raw = {
            "bot": {
                "dry_run": False,
                "delete_duplicates": True,
                "api_id": "123",
                "api_hash": "abc",
                "bot_token": "token",
                "admin_id": "456",
                "target_chat_ids": ["-1001", "-1002"],
                "web_tg_session": "./sessions/webui",
            },
            "pipeline": {"worker_count": 2},
        }
        settings = load_runtime_settings(raw)
        self.assertFalse(settings.dry_run)
        self.assertTrue(settings.delete_duplicates)
        self.assertEqual(settings.api_id, 123)
        self.assertEqual(settings.api_hash, "abc")
        self.assertEqual(settings.bot_token, "token")
        self.assertEqual(settings.admin_id, "456")
        self.assertEqual(settings.target_chat_tokens, ["-1001", "-1002"])
        self.assertEqual(settings.web_tg_session, "./sessions/webui")
        self.assertEqual(settings.pipeline_worker_count, 2)

    def test_runtime_settings_normalizes_legacy_target_chat_id(self) -> None:
        raw = {
            "bot": {
                "dry_run": True,
                "target_chat_id": "-1009",
            }
        }
        settings = load_runtime_settings(raw)
        self.assertTrue(settings.dry_run)
        self.assertEqual(settings.target_chat_tokens, ["-1009"])
        self.assertEqual(settings.pipeline_worker_count, 1)


if __name__ == "__main__":
    unittest.main()
