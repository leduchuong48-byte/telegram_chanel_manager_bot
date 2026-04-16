from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerOperatorSemanticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_job_detail_exposes_unified_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-ok", chat_id=1, task_type="delete_by_type", payload_json="{}", priority=1)
            db.update_job_status("job-ok", "running")
            db.update_job_progress("job-ok", scanned=20, matched=8, acted=8, failed=0)
            db.update_job_status("job-ok", "completed")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.get_cleaner_job(job_id="job-ok", _="admin")
            self.assertIn("status_label", payload)
            self.assertIn("business_result_label", payload)
            self.assertIn("operator_summary", payload)
            self.assertIn("next_action_summary", payload)
            self.assertEqual(payload["status_label"], "已完成")
            self.assertEqual(payload["business_result_label"], "已处理")


if __name__ == "__main__":
    unittest.main()
