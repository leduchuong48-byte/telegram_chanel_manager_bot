from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import warnings
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.main import create_app
from app.routers import cleaner


class AppRuntimeWiringTest(unittest.TestCase):
    def test_create_app_initializes_cleaner_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "web_admin": {"secret_key": "test-secret"},
                        "bot": {},
                        "database": {"path": str(root / "bot.db")},
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            create_app(manager)
            runtime = cleaner._get_pipeline_runtime()
            self.assertIsNotNone(runtime)

    def test_create_app_reads_pipeline_worker_count_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "web_admin": {"secret_key": "test-secret"},
                        "bot": {},
                        "database": {"path": str(root / "bot.db")},
                        "pipeline": {"worker_count": 3},
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            create_app(manager)
            runtime = cleaner._get_pipeline_runtime()
            self.assertEqual(runtime._worker_count, 3)


class AppRuntimeLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def test_create_app_uses_lifespan_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "web_admin": {"secret_key": "test-secret"},
                        "bot": {},
                        "database": {"path": str(root / "bot.db")},
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                app = create_app(manager)
            self.assertIsNotNone(app.router.lifespan_context)
            messages = [str(item.message) for item in caught]
            self.assertFalse(any("on_event is deprecated" in msg for msg in messages))

    async def test_runtime_starts_and_stops_with_app_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "web_admin": {"secret_key": "test-secret"},
                        "bot": {},
                        "database": {"path": str(root / "bot.db")},
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            app = create_app(manager)
            runtime = cleaner._get_pipeline_runtime()
            async with app.router.lifespan_context(app):
                self.assertTrue(runtime.is_running)
            self.assertFalse(runtime.is_running)


if __name__ == "__main__":
    unittest.main()
