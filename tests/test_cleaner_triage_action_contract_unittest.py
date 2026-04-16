from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerTriageActionContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_queue_items_have_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-failed", chat_id=100, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-failed", "failed", error="runtime error")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.list_review_queue_jobs(limit=20, _="admin")
            self.assertGreaterEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertIn("action_label", item)
            self.assertIn("action_href", item)
            self.assertIn("next_action_summary", item)
            self.assertTrue(str(item["next_action_summary"]).strip())
            self.assertTrue(str(item["action_href"]).startswith("/"))

    async def test_dead_letters_items_have_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-dl", chat_id=777, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-dl", "dead_letter", error="permanent failure")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.list_dead_letter_jobs(limit=20, _="admin")
            self.assertEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertIn("action_label", item)
            self.assertIn("action_href", item)
            self.assertIn("next_action_summary", item)
            self.assertTrue(str(item["next_action_summary"]).strip())
            self.assertTrue(str(item["action_href"]).startswith("/"))


if __name__ == "__main__":
    unittest.main()
