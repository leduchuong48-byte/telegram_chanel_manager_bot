from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import settings as settings_router
from tg_media_dedupe_bot.db import Database


class LlmSettingsApiTest(unittest.TestCase):
    def test_get_llm_settings_reads_provider_and_masks_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            secrets_dir = root / "provider_secrets"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            manager = ConfigManager(config_path)
            settings_router.set_config_manager(manager)
            settings_router._PROVIDER_SECRETS_DIR = secrets_dir

            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="local-ai",
                    display_name="local-ai",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-5.2",
                )
            finally:
                db.close()

            secrets_dir.mkdir(parents=True, exist_ok=True)
            (secrets_dir / "local-ai.json").write_text(
                json.dumps({
                    "provider_key": "local-ai",
                    "api_key": "sk-abc",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-5.2",
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            resp = asyncio.run(settings_router.get_llm_settings(_="admin"))
            self.assertEqual(resp.data["provider_key"], "local-ai")
            self.assertEqual(resp.data["base_url"], "https://api.openai.com/v1")
            self.assertEqual(resp.data["model"], "gpt-5.2")
            self.assertEqual(resp.data["api_key"], "*****")

    def test_update_llm_settings_upserts_provider_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            secrets_dir = root / "provider_secrets"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            manager = ConfigManager(config_path)
            settings_router.set_config_manager(manager)
            settings_router._PROVIDER_SECRETS_DIR = secrets_dir

            result = asyncio.run(
                settings_router.update_llm_settings(
                    provider_key="local-ai",
                    base_url="https://api.openai.com/v1",
                    api_key="sk-xyz",
                    model="gpt-5.2",
                    _="admin",
                )
            )
            self.assertTrue(result.success)

            db = Database(db_path)
            try:
                provider = db.get_provider(provider_key="local-ai")
            finally:
                db.close()
            self.assertIsNotNone(provider)
            self.assertEqual(provider["default_model"], "gpt-5.2")
            self.assertEqual(provider["base_url"], "https://api.openai.com/v1")

            secret_file = secrets_dir / "local-ai.json"
            self.assertTrue(secret_file.exists())
            secret_data = json.loads(secret_file.read_text(encoding="utf-8"))
            self.assertEqual(secret_data["api_key"], "sk-xyz")


if __name__ == "__main__":
    unittest.main()
