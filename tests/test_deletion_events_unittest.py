from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database


class DeletionEventsDbTest(unittest.TestCase):
    def test_record_and_list_deletion_events_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            db = Database(db_path)
            try:
                db.record_deletion_event(
                    chat_id=-1001,
                    message_id=1,
                    event_type="matched_text_blacklist",
                    reason="media_blacklist:text",
                    result="matched",
                    detail=None,
                )
                time.sleep(0.01)
                db.record_deletion_event(
                    chat_id=-1001,
                    message_id=1,
                    event_type="skipped_delete_disabled",
                    reason="media_blacklist:text",
                    result="skipped",
                    detail="delete_enabled=0",
                )
                rows = db.list_deletion_events(chat_id=-1001, limit=10)
            finally:
                db.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["event_type"], "skipped_delete_disabled")
            self.assertEqual(rows[1]["event_type"], "matched_text_blacklist")

    def test_list_deletion_events_filters_by_event_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            db = Database(db_path)
            try:
                db.record_deletion_event(
                    chat_id=-1001,
                    message_id=10,
                    event_type="delete_succeeded",
                    reason="ad_block",
                    result="success",
                    detail=None,
                )
                db.record_deletion_event(
                    chat_id=-1001,
                    message_id=11,
                    event_type="delete_failed",
                    reason="ad_block",
                    result="failed",
                    detail="forbidden",
                )
                rows = db.list_deletion_events(chat_id=-1001, event_type="delete_failed", limit=10)
            finally:
                db.close()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "delete_failed")
            self.assertEqual(rows[0]["message_id"], 11)


if __name__ == "__main__":
    unittest.main()
