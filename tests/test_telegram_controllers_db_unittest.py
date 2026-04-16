from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database


class TelegramControllersDbTest(unittest.TestCase):
    def test_upsert_and_list_with_primary_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                db.upsert_telegram_controller(user_id=1001, display_name="alice", enabled=True, is_primary=True, source="manual", role="owner")
                db.upsert_telegram_controller(user_id=1002, display_name="bob", enabled=True, is_primary=False, source="manual", role="operator")
                rows = db.list_telegram_controllers(enabled_only=False)
            finally:
                db.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_id"], 1001)
        self.assertTrue(rows[0]["is_primary"])
        self.assertEqual(rows[1]["user_id"], 1002)
        self.assertEqual(rows[0]["role"], "owner")
        self.assertEqual(rows[1]["role"], "operator")

    def test_make_primary_switches_existing_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                db.upsert_telegram_controller(user_id=1001, display_name="alice", enabled=True, is_primary=True, source="manual", role="owner")
                db.upsert_telegram_controller(user_id=1002, display_name="bob", enabled=True, is_primary=False, source="manual", role="operator")
                db.set_primary_telegram_controller(user_id=1002)
                rows = db.list_telegram_controllers(enabled_only=False)
            finally:
                db.close()

        self.assertEqual(rows[0]["user_id"], 1002)
        self.assertTrue(rows[0]["is_primary"])
        self.assertFalse(next(r for r in rows if r["user_id"] == 1001)["is_primary"])

    def test_delete_rejects_only_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                db.upsert_telegram_controller(user_id=1001, display_name="alice", enabled=True, is_primary=True, source="manual", role="owner")
                with self.assertRaises(ValueError):
                    db.delete_telegram_controller(user_id=1001)
            finally:
                db.close()

    def test_disable_rejects_only_enabled_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                db.upsert_telegram_controller(user_id=1001, display_name="alice", enabled=True, is_primary=True, source="manual", role="owner")
                with self.assertRaises(ValueError):
                    db.set_telegram_controller_enabled(user_id=1001, enabled=False)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
