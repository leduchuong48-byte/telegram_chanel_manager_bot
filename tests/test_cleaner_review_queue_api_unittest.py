from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerReviewQueueApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_queue_collects_suspicious_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-noop", chat_id=100, task_type="delete_by_type", payload_json="{}", priority=1)
            db.update_job_progress("job-noop", scanned=12, matched=0, acted=0, failed=0)
            db.update_job_status("job-noop", "completed", error=None)

            db.create_job(job_id="job-failed", chat_id=100, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-failed", "failed", error="runtime error")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            review = await cleaner_router.list_review_queue_jobs(limit=20, _="admin")
            self.assertGreaterEqual(review["count"], 2)
            reasons = {item["job_id"]: item["reason"] for item in review["items"]}
            self.assertEqual(reasons.get("job-noop"), "no_match_no_action")
            self.assertEqual(reasons.get("job-failed"), "runtime_failure")


if __name__ == "__main__":
    unittest.main()
