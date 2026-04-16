from __future__ import annotations

import unittest

from tg_media_dedupe_bot.controller_auth import can_run_command, resolve_controller_policy


class ControllerAuthPolicyTest(unittest.TestCase):
    def test_prefers_enabled_controller_rows(self) -> None:
        policy = resolve_controller_policy(
            controller_rows=[
                {"user_id": 2002, "enabled": True, "is_primary": False, "role": "operator"},
                {"user_id": 2001, "enabled": True, "is_primary": True, "role": "owner"},
            ],
            legacy_controller_id="1001",
            current_user_id=3001,
        )
        self.assertEqual(policy.allowed_ids, {2001, 2002})
        self.assertEqual(policy.primary_id, 2001)
        self.assertFalse(policy.auto_bind_legacy)
        self.assertEqual(policy.roles_by_user_id[2001], "owner")
        self.assertEqual(policy.roles_by_user_id[2002], "operator")

    def test_fallbacks_to_legacy_when_no_enabled_rows(self) -> None:
        policy = resolve_controller_policy(
            controller_rows=[
                {"user_id": 2002, "enabled": False, "is_primary": True},
            ],
            legacy_controller_id="1001",
            current_user_id=3001,
        )
        self.assertEqual(policy.allowed_ids, {1001})
        self.assertEqual(policy.primary_id, 1001)
        self.assertFalse(policy.auto_bind_legacy)

    def test_auto_bind_current_user_when_no_config(self) -> None:
        policy = resolve_controller_policy(
            controller_rows=[],
            legacy_controller_id=None,
            current_user_id=3001,
        )
        self.assertEqual(policy.allowed_ids, {3001})
        self.assertEqual(policy.primary_id, 3001)
        self.assertTrue(policy.auto_bind_legacy)

    def test_role_command_matrix(self) -> None:
        self.assertTrue(can_run_command("owner", "dangerous"))
        self.assertTrue(can_run_command("admin", "config"))
        self.assertFalse(can_run_command("operator", "system"))
        self.assertFalse(can_run_command("readonly", "dangerous"))
        self.assertTrue(can_run_command("readonly", "query"))

if __name__ == "__main__":
    unittest.main()
