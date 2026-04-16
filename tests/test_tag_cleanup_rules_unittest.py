from __future__ import annotations

import unittest

from app.services import tag_cleanup_rules


class TagCleanupRulesTest(unittest.TestCase):
    def test_normalize_and_dedupe_tags(self) -> None:
        normalized = tag_cleanup_rules.normalize_input_tags([
            " AI绘图 ",
            "ai绘图",
            "",
            "#AI作图",
            "AI作图",
        ])
        self.assertEqual(normalized, ["ai绘图", "ai作图"])

    def test_reject_self_target_for_rename_merge(self) -> None:
        items = [
            {
                "source_tag": "ai绘图",
                "suggested_action": "rename",
                "suggested_target_tag": "ai绘图",
                "confidence": 0.9,
            },
            {
                "source_tag": "ai作图",
                "suggested_action": "merge",
                "suggested_target_tag": "ai作图",
                "confidence": 0.9,
            },
        ]
        cleaned = tag_cleanup_rules.clean_suggestions(items)
        self.assertEqual(cleaned[0]["suggested_action"], "keep")
        self.assertEqual(cleaned[1]["suggested_action"], "keep")


if __name__ == "__main__":
    unittest.main()
