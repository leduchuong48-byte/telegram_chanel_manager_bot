from __future__ import annotations

import unittest

from app.core.chat_effective_state import build_chat_effective_summary


class ChatEffectiveStateTest(unittest.TestCase):
    def test_text_policy_without_delete_returns_matched_but_not_deleting(self) -> None:
        summary = build_chat_effective_summary(
            chat_id=-1003574325789,
            bot_config={"dry_run": False, "delete_duplicates": False},
            chat_settings={
                "media_blacklist": "audio,document,photo,text",
                "delete_duplicates": None,
                "dry_run": None,
            },
            managed_chat={"bot_status": "administrator", "bot_can_manage": True},
        )
        self.assertEqual(summary["effective"]["result"], "matched_but_not_deleting")
        self.assertIn("text_policy_without_delete", summary["effective"]["conflicts"])
        self.assertEqual(summary["enforcement"]["mode"], "observe")
        self.assertEqual(summary["enforcement"]["source"], "global_default")

    def test_chat_override_delete_true_returns_deleting(self) -> None:
        summary = build_chat_effective_summary(
            chat_id=-1003000143874,
            bot_config={"dry_run": False, "delete_duplicates": False},
            chat_settings={
                "media_blacklist": "text",
                "delete_duplicates": "1",
                "dry_run": "0",
            },
            managed_chat={"bot_status": "administrator", "bot_can_manage": True},
        )
        self.assertEqual(summary["effective"]["result"], "deleting")
        self.assertEqual(summary["enforcement"]["mode"], "delete")
        self.assertEqual(summary["enforcement"]["source"], "chat_override")

    def test_permission_blocked_when_bot_cannot_manage(self) -> None:
        summary = build_chat_effective_summary(
            chat_id=-1003000143874,
            bot_config={"dry_run": False, "delete_duplicates": True},
            chat_settings={
                "media_blacklist": "text",
                "delete_duplicates": None,
                "dry_run": None,
            },
            managed_chat={"bot_status": "member", "bot_can_manage": False},
        )
        self.assertEqual(summary["effective"]["result"], "permission_blocked")
        self.assertIn("delete_enabled_but_bot_cannot_manage", summary["effective"]["conflicts"])

    def test_unconfigured_when_text_policy_disabled(self) -> None:
        summary = build_chat_effective_summary(
            chat_id=-1003000143874,
            bot_config={"dry_run": False, "delete_duplicates": True},
            chat_settings={
                "media_blacklist": "audio,photo",
                "delete_duplicates": None,
                "dry_run": None,
            },
            managed_chat={"bot_status": "administrator", "bot_can_manage": True},
        )
        self.assertEqual(summary["effective"]["result"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
