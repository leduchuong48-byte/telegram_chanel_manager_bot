from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import chat_effective_state as chat_effective_state_router
from tg_media_dedupe_bot.db import Database


class ChatEffectiveStateEventsApiTest(unittest.TestCase):
    def test_events_endpoint_returns_recent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            try:
                db.record_deletion_event(
                    chat_id=-1003574325789,
                    message_id=101,
                    event_type="matched_text_blacklist",
                    reason="media_blacklist:text",
                    result="matched",
                    detail=None,
                )
                db.record_deletion_event(
                    chat_id=-1003574325789,
                    message_id=101,
                    event_type="skipped_delete_disabled",
                    reason="media_blacklist:text",
                    result="skipped",
                    detail="delete_enabled=0",
                )
            finally:
                db.close()

            manager = ConfigManager(config_path)
            chat_effective_state_router.set_config_manager(manager)

            payload = asyncio.run(
                chat_effective_state_router.list_chat_deletion_events(
                    _="admin",
                    chat_id=-1003574325789,
                    limit=20,
                    event_type=None,
                )
            )
            self.assertTrue(payload["success"])
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["items"][0]["event_type"], "skipped_delete_disabled")


if __name__ == "__main__":
    unittest.main()
