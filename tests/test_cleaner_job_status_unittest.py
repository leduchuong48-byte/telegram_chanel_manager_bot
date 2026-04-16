from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerJobStatusTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_get_job_status_returns_payload(self) -> None:
        payload = cleaner.BatchDeleteRequest(count=10, target="-1001234567890")
        created = await cleaner.batch_delete(payload, _="admin")

        self.db.update_job_progress(created["job_id"], scanned=10, matched=10, acted=7, failed=0)

        status_payload = await cleaner.get_cleaner_job(created["job_id"], _="admin")
        self.assertEqual(status_payload["job_id"], created["job_id"])
        self.assertEqual(status_payload["status"], "pending")
        self.assertEqual(status_payload["task_type"], "batch_delete")
        self.assertEqual(status_payload["progress"], {"scanned": 10, "matched": 10, "acted": 7, "failed": 0})

    async def test_cancel_job_endpoint_marks_job_cancelled(self) -> None:
        payload = cleaner.BatchDeleteRequest(count=5, target="-1001234567890")
        created = await cleaner.batch_delete(payload, _="admin")

        result = await cleaner.cancel_cleaner_job(created["job_id"], _="admin")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "cancelled")

        row = self.db.get_job(created["job_id"])
        self.assertEqual(row["status"], "cancelled")

    async def test_list_jobs_returns_recent_jobs(self) -> None:
        first = await cleaner.batch_delete(cleaner.BatchDeleteRequest(count=3, target="-1001234567890"), _="admin")
        second = await cleaner.delete_by_type(
            cleaner.DeleteByTypeRequest(types=["text"], limit=5, target="-1001234567890"),
            _="admin",
        )

        result = await cleaner.list_cleaner_jobs(limit=10, _="admin")
        self.assertIn("jobs", result)
        job_ids = [item["job_id"] for item in result["jobs"]]
        self.assertIn(first["job_id"], job_ids)
        self.assertIn(second["job_id"], job_ids)

    async def test_list_jobs_filters_by_status_task_type_and_chat_id(self) -> None:
        first = await cleaner.batch_delete(cleaner.BatchDeleteRequest(count=3, target="-1001234567890"), _="admin")
        second = await cleaner.delete_by_type(
            cleaner.DeleteByTypeRequest(types=["text"], limit=5, target="-1002000000001"),
            _="admin",
        )

        self.db.update_job_status(first["job_id"], "failed", error="boom")
        self.db.update_job_status(second["job_id"], "completed")

        by_status = await cleaner.list_cleaner_jobs(limit=10, status="failed", _="admin")
        self.assertEqual([item["job_id"] for item in by_status["jobs"]], [first["job_id"]])

        by_type = await cleaner.list_cleaner_jobs(limit=10, task_type="delete_by_type", _="admin")
        self.assertEqual([item["job_id"] for item in by_type["jobs"]], [second["job_id"]])

        by_chat = await cleaner.list_cleaner_jobs(limit=10, chat_id=-1002000000001, _="admin")
        self.assertEqual([item["job_id"] for item in by_chat["jobs"]], [second["job_id"]])

    async def test_monitoring_summary_reports_running_paused_and_failed(self) -> None:
        first = await cleaner.batch_delete(cleaner.BatchDeleteRequest(count=3, target="-1001234567890"), _="admin")
        second = await cleaner.delete_by_type(
            cleaner.DeleteByTypeRequest(types=["text"], limit=5, target="-1002000000001"),
            _="admin",
        )

        self.db.update_job_status(first["job_id"], "running")
        self.db.update_job_status(second["job_id"], "failed", error="boom")
        self.runtime.pause_chat(-1001234567890, 30)

        summary = await cleaner.get_cleaner_monitoring(_="admin")
        self.assertEqual(summary["running_jobs"], 1)
        self.assertEqual(summary["paused_chats"], 1)
        self.assertEqual(summary["recent_failed_jobs"], 1)


if __name__ == "__main__":
    unittest.main()
