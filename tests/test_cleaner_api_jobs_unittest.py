from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerJobsApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_jobs_includes_error_and_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-1", chat_id=-1001, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-1", "running")
            db.update_job_progress("job-1", scanned=10, matched=8, acted=7, failed=1)
            db.update_job_status("job-1", "failed", error="mock failed")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.list_cleaner_jobs(limit=20, status=None, task_type=None, chat_id=None, _="admin")
            self.assertIn("jobs", payload)
            self.assertEqual(len(payload["jobs"]), 1)
            job = payload["jobs"][0]
            self.assertEqual(job["job_id"], "job-1")
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["last_error"], "mock failed")
            self.assertGreaterEqual(int(job["created_at"]), 1)
            self.assertGreaterEqual(int(job["started_at"]), 1)
            self.assertGreaterEqual(int(job["finished_at"]), 1)
            self.assertIn("outcome_category", job)
            self.assertIn("outcome_summary", job)
            self.assertIn("did_start", job)
            self.assertIn("did_finish", job)
            self.assertIn("did_work", job)
            self.assertIn("business_result", job)
            self.assertIn("operator_summary", job)
            self.assertIn("next_action_summary", job)
            self.assertIn("timing", job)
            self.assertTrue(job["did_start"])
            self.assertTrue(job["did_finish"])
            self.assertTrue(job["did_work"])
            self.assertEqual(job["business_result"], "failed")

    async def test_list_jobs_marks_completed_no_match_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-2", chat_id=-1002, task_type="delete_by_type", payload_json="{}", priority=1)
            db.update_job_status("job-2", "running")
            db.update_job_progress("job-2", scanned=9, matched=0, acted=0, failed=0)
            db.update_job_status("job-2", "completed")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.list_cleaner_jobs(limit=20, status=None, task_type=None, chat_id=None, _="admin")
            job = payload["jobs"][0]
            self.assertEqual(job["outcome_category"], "no_matches")
            self.assertIn("没有命中", job["outcome_summary"])
            self.assertTrue(job["did_start"])
            self.assertTrue(job["did_finish"])
            self.assertFalse(job["did_work"])
            self.assertEqual(job["business_result"], "no_op_no_match")
            self.assertIn("未命中", job["operator_summary"])


if __name__ == "__main__":
    unittest.main()
