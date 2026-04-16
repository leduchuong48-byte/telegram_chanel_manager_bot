from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import chat_effective_state as chat_effective_state_router
from tg_media_dedupe_bot.db import Database


class ChatEffectiveStateApiTest(unittest.TestCase):
    def test_list_and_detail_endpoint_return_effective_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "bot": {
                            "dry_run": False,
                            "delete_duplicates": False,
                        },
                        "database": {
                            "path": str(db_path),
                        },
                    }
                ),
                encoding="utf-8",
            )

            db = Database(db_path)
            try:
                chat_id = -1003574325789
                db.upsert_managed_chat(
                    chat_id=chat_id,
                    title="男女资源",
                    username="",
                    chat_type="supergroup",
                    source="test",
                    is_active=True,
                    bot_status="administrator",
                    bot_can_manage=True,
                )
                db.set_setting(f"chat:{chat_id}:media_blacklist", "audio,document,photo,text")
            finally:
                db.close()

            manager = ConfigManager(config_path)
            chat_effective_state_router.set_config_manager(manager)

            list_data = asyncio.run(chat_effective_state_router.list_chat_effective_states(_="admin", limit=200, active_only=True))
            self.assertTrue(list_data["success"])
            self.assertGreaterEqual(list_data["count"], 1)

            detail_data = asyncio.run(chat_effective_state_router.get_chat_effective_state(chat_id=chat_id, _="admin"))
            self.assertTrue(detail_data["success"])
            self.assertEqual(detail_data["item"]["chat_id"], chat_id)
            self.assertEqual(detail_data["item"]["effective"]["result"], "matched_but_not_deleting")
            self.assertIn("text_policy_without_delete", detail_data["item"]["effective"]["conflicts"])


if __name__ == "__main__":
    unittest.main()
