from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import ai_health as ai_health_router
from tg_media_dedupe_bot.db import Database


class AiHealthApiTest(unittest.TestCase):
    def test_ai_health_returns_zero_summary_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            summary = asyncio.run(ai_health_router.get_ai_health(_="admin"))

            self.assertEqual(summary.providers_total, 0)
            self.assertEqual(summary.providers_healthy, 0)
            self.assertEqual(summary.providers_degraded, 0)
            self.assertEqual(summary.review_queue_pending, 0)

    def test_ai_health_counts_enabled_and_disabled_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1",
                )
                db.upsert_provider(
                    provider_key="anthropic_backup",
                    display_name="Anthropic Backup",
                    provider_type="anthropic",
                    base_url="https://api.openai.com/v1",
                    enabled=False,
                    use_responses_mode="off",
                    default_model="claude-3-7-sonnet",
                )
            finally:
                db.close()

            summary = asyncio.run(ai_health_router.get_ai_health(_="admin"))

            self.assertEqual(summary.providers_total, 2)
            self.assertEqual(summary.providers_healthy, 1)
            self.assertEqual(summary.providers_degraded, 1)


if __name__ == "__main__":
    unittest.main()
