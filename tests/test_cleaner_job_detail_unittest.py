from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerJobDetailTest(unittest.IsolatedAsyncioTestCase):
    async def test_job_detail_includes_stage_failure_category_and_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-d1", chat_id=-1001, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-d1", "running")
            db.update_job_progress("job-d1", scanned=20, matched=10, acted=8, failed=2)
            db.update_job_status("job-d1", "failed", error="unable to resolve target")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.get_cleaner_job(job_id="job-d1", _="admin")
            self.assertEqual(payload["job_id"], "job-d1")
            self.assertIn("stage", payload)
            self.assertIn("failure_category", payload)
            self.assertIn("recommended_action", payload)
            self.assertIn("timeline", payload)
            self.assertEqual(payload["failure_category"], "chat_unresolved")
            self.assertTrue(isinstance(payload["timeline"], list) and len(payload["timeline"]) >= 2)
            self.assertIn("related_links", payload)
            self.assertIn("session", payload["related_links"])
            self.assertIn("chat_visibility", payload["related_links"])
            self.assertIn("outcome_category", payload)
            self.assertIn("outcome_summary", payload)
            self.assertIn("did_start", payload)
            self.assertIn("did_finish", payload)
            self.assertIn("did_work", payload)
            self.assertIn("business_result", payload)
            self.assertIn("operator_summary", payload)
            self.assertIn("next_action_summary", payload)
            self.assertIn("timing", payload)
            self.assertTrue(payload["did_start"])
            self.assertTrue(payload["did_finish"])
            self.assertTrue(payload["did_work"])
            self.assertEqual(payload["business_result"], "failed")

    async def test_job_detail_marks_completed_no_match_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-d2", chat_id=-1002, task_type="delete_by_type", payload_json="{}", priority=1)
            db.update_job_status("job-d2", "running")
            db.update_job_progress("job-d2", scanned=5, matched=0, acted=0, failed=0)
            db.update_job_status("job-d2", "completed")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.get_cleaner_job(job_id="job-d2", _="admin")
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["outcome_category"], "no_matches")
            self.assertIn("没有命中", payload["outcome_summary"])
            self.assertTrue(payload["did_start"])
            self.assertTrue(payload["did_finish"])
            self.assertFalse(payload["did_work"])
            self.assertEqual(payload["business_result"], "no_op_no_match")
            self.assertIn("未命中", payload["operator_summary"])


if __name__ == "__main__":
    unittest.main()
