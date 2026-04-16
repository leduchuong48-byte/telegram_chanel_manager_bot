from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerJobSubmissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.config_path = root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "web_admin": {"secret_key": "test-secret"},
                    "bot": {"target_chat_ids": ["-1001234567890"]},
                    "database": {"path": str(root / "bot.db")},
                }
            ),
            encoding="utf-8",
        )
        self.manager = ConfigManager(self.config_path)
        self.db = Database(root / "bot.db")
        self.runtime = PipelineRuntime(self.db)
        cleaner.set_config_manager(self.manager)
        cleaner.set_pipeline_runtime(self.runtime)

    async def asyncTearDown(self) -> None:
        cleaner.set_pipeline_runtime(None)
        self.db.close()
        self.tmpdir.cleanup()

    async def test_batch_delete_creates_pending_job(self) -> None:
        payload = cleaner.BatchDeleteRequest(count=25, target="-1001234567890")
        result = await cleaner.batch_delete(payload, _="admin")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["job_type"], "batch_delete")
        row = self.db.get_job(result["job_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["chat_id"], -1001234567890)

    async def test_delete_by_type_creates_pending_job(self) -> None:
        payload = cleaner.DeleteByTypeRequest(types=["text", "photo"], limit=50, target="-1001234567890")
        result = await cleaner.delete_by_type(payload, _="admin")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["job_type"], "delete_by_type")
        row = self.db.get_job(result["job_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["task_type"], "delete_by_type")
        self.assertEqual(row["chat_id"], -1001234567890)


if __name__ == "__main__":
    unittest.main()
