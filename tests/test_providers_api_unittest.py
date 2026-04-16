from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.core.config_manager import ConfigManager
from app.routers import providers as providers_router


class ProvidersApiTest(unittest.TestCase):
    def test_create_and_list_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(
                json.dumps({"database": {"path": str(db_path)}}),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            providers_router.set_config_manager(manager)

            created = asyncio.run(
                providers_router.create_provider(
                    providers_router.ProviderCreateRequest(
                        provider_key="openai_main",
                        display_name="OpenAI Main",
                        provider_type="openai_compatible",
                        base_url="https://api.openai.com/v1",
                        enabled=True,
                        use_responses_mode="auto",
                        default_model="gpt-4.1",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(created.success)

            listed = asyncio.run(providers_router.list_providers(_="admin", enabled_only=False))
            self.assertEqual(listed.count, 1)
            self.assertEqual(listed.data[0].provider_key, "openai_main")
            self.assertEqual(listed.data[0].use_responses_mode, "auto")

    def test_create_provider_rejects_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(
                json.dumps({"database": {"path": str(db_path)}}),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            providers_router.set_config_manager(manager)

            payload = providers_router.ProviderCreateRequest(
                provider_key="openai_main",
                display_name="OpenAI Main",
                provider_type="openai_compatible",
                base_url="https://api.openai.com/v1",
                enabled=True,
                use_responses_mode="auto",
                default_model="gpt-4.1",
            )
            asyncio.run(providers_router.create_provider(payload, _="admin"))

            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(providers_router.create_provider(payload, _="admin"))
            self.assertEqual(ctx.exception.status_code, 409)


    def test_update_and_probe_provider_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(
                json.dumps({"database": {"path": str(db_path)}}),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            providers_router.set_config_manager(manager)

            asyncio.run(
                providers_router.create_provider(
                    providers_router.ProviderCreateRequest(
                        provider_key="openai_main",
                        display_name="OpenAI Main",
                        provider_type="openai_compatible",
                        base_url="https://api.openai.com/v1",
                        enabled=True,
                        use_responses_mode="auto",
                        default_model="gpt-4.1",
                    ),
                    _="admin",
                )
            )

            updated = asyncio.run(
                providers_router.update_provider(
                    provider_key="openai_main",
                    payload=providers_router.ProviderUpdateRequest(
                        display_name="OpenAI Primary",
                        enabled=False,
                        use_responses_mode="off",
                        default_model="gpt-4.1-mini",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(updated.success)

            tested = asyncio.run(providers_router.test_provider_connection(provider_key="openai_main", _="admin"))
            self.assertTrue(tested.success)

            probed = asyncio.run(providers_router.probe_provider_capabilities(provider_key="openai_main", _="admin"))
            self.assertTrue(probed.success)

            listed = asyncio.run(providers_router.list_providers(_="admin", enabled_only=False))
            self.assertEqual(listed.count, 1)
            self.assertEqual(listed.data[0].display_name, "OpenAI Primary")
            self.assertFalse(listed.data[0].enabled)
            self.assertEqual(listed.data[0].use_responses_mode, "off")
            self.assertEqual(listed.data[0].last_test_status, "ok")
            self.assertEqual(listed.data[0].last_probe_status, "ok")
            self.assertTrue(listed.data[0].supports_responses)


    def test_provider_api_key_saved_to_secret_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            secrets_dir = root / "provider_secrets"
            config_path.write_text(
                json.dumps({"database": {"path": str(db_path)}}),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            providers_router.set_config_manager(manager)
            providers_router._PROVIDER_SECRETS_DIR = secrets_dir

            created = asyncio.run(
                providers_router.create_provider(
                    providers_router.ProviderCreateRequest(
                        provider_key="local-ai",
                        display_name="local-ai",
                        provider_type="openai_compatible",
                        base_url="https://api.openai.com/v1",
                        enabled=True,
                        use_responses_mode="auto",
                        default_model="gpt-5.2",
                        api_key="sk-test-secret",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(created.success)

            listed = asyncio.run(providers_router.list_providers(_="admin", enabled_only=False))
            self.assertEqual(listed.count, 1)
            self.assertTrue(listed.data[0].has_api_key)

            secret_file = secrets_dir / "local-ai.json"
            self.assertTrue(secret_file.exists())
            raw = json.loads(secret_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["provider_key"], "local-ai")
            self.assertEqual(raw["api_key"], "sk-test-secret")


if __name__ == "__main__":
    unittest.main()
