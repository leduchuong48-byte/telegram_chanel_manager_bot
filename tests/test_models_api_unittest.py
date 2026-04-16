from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import models as models_router
from tg_media_dedupe_bot.db import Database


class ModelsApiTest(unittest.TestCase):
    def test_sync_models_from_provider_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            models_router.set_config_manager(manager)

            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
            finally:
                db.close()

            before = asyncio.run(models_router.list_models(_="admin", provider_key=None, enabled_only=False))
            self.assertEqual(before.count, 0)

            sync_result = asyncio.run(models_router.sync_models(_="admin"))
            self.assertTrue(sync_result.success)

            after = asyncio.run(models_router.list_models(_="admin", provider_key=None, enabled_only=False))
            self.assertEqual(after.count, 1)
            self.assertEqual(after.data[0].provider_key, "openai_main")
            self.assertEqual(after.data[0].model_id, "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()
