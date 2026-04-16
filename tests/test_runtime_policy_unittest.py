from __future__ import annotations

import unittest

from app.services import runtime_policy


class RuntimePolicyTest(unittest.TestCase):
    def test_request_override_has_highest_priority(self) -> None:
        effective = runtime_policy.resolve_responses_mode(
            request_mode="force_off",
            model_mode="force_on",
            provider_mode="auto",
            global_mode="auto",
        )
        self.assertEqual(effective, "force_off")

    def test_model_override_used_when_request_absent(self) -> None:
        effective = runtime_policy.resolve_responses_mode(
            request_mode=None,
            model_mode="force_on",
            provider_mode="off",
            global_mode="auto",
        )
        self.assertEqual(effective, "force_on")

    def test_provider_override_used_when_higher_levels_absent(self) -> None:
        effective = runtime_policy.resolve_responses_mode(
            request_mode=None,
            model_mode=None,
            provider_mode="off",
            global_mode="auto",
        )
        self.assertEqual(effective, "off")

    def test_defaults_to_global_when_no_other_value(self) -> None:
        effective = runtime_policy.resolve_responses_mode(
            request_mode=None,
            model_mode=None,
            provider_mode=None,
            global_mode="auto",
        )
        self.assertEqual(effective, "auto")


if __name__ == "__main__":
    unittest.main()
