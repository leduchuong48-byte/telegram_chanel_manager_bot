from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.config import load_config


class LegacyConfigTest(unittest.TestCase):
    def test_load_config_prefers_config_json_business_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "TG_BOT_TOKEN=env-token",
                        "TG_API_ID=999",
                        "TG_API_HASH=env-hash",
                        "DRY_RUN=1",
                        "DELETE_DUPLICATES=1",
                        "TG_SESSION=./data/telethon.session",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "bot": {
                            "bot_token": "cfg-token",
                            "api_id": "123",
                            "api_hash": "cfg-hash",
                            "dry_run": False,
                            "delete_duplicates": False,
                            "web_tg_session": "./sessions/webui",
                        },
                        "database": {"path": "./data/bot.db"},
                    }
                ),
                encoding="utf-8",
            )

            old_cwd = Path.cwd()
            old_env = dict(os.environ)
            try:
                os.chdir(root)
                for key in [
                    "TG_BOT_TOKEN",
                    "TG_API_ID",
                    "TG_API_HASH",
                    "DRY_RUN",
                    "DELETE_DUPLICATES",
                    "TG_SESSION",
                ]:
                    os.environ.pop(key, None)
                cfg = load_config()
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(cfg.bot_token, "cfg-token")
            self.assertEqual(cfg.tg_api_id, 123)
            self.assertEqual(cfg.tg_api_hash, "cfg-hash")
            self.assertFalse(cfg.dry_run)
            self.assertFalse(cfg.delete_duplicates)

    def test_load_config_warns_on_deprecated_env_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "DRY_RUN=1",
                        "DELETE_DUPLICATES=1",
                        "TG_API_ID=999",
                        "TG_API_HASH=env-hash",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "bot": {
                            "dry_run": False,
                            "delete_duplicates": False,
                            "api_id": "123",
                            "api_hash": "cfg-hash",
                        }
                    }
                ),
                encoding="utf-8",
            )

            old_cwd = Path.cwd()
            old_env = dict(os.environ)
            try:
                os.chdir(root)
                for key in ["DRY_RUN", "DELETE_DUPLICATES", "TG_API_ID", "TG_API_HASH"]:
                    os.environ.pop(key, None)
                with self.assertLogs("tg_media_dedupe_bot.config", level="WARNING") as logs:
                    load_config()
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_env)

            output = "\n".join(logs.output)
            self.assertIn("deprecated_env_override_ignored", output)


if __name__ == "__main__":
    unittest.main()
