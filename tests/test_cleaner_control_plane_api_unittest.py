from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerControlPlaneApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_dead_letters_and_locks_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-dl1", chat_id=777, task_type="batch_delete", payload_json="{}", priority=1)
            db.update_job_status("job-dl1", "dead_letter", error="permanent failure")
            db.acquire_target_lock(lock_key="chat:777", job_id="job-dl1", worker_id="w1", lease_seconds=30)

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            dead = await cleaner_router.list_dead_letter_jobs(limit=20, _="admin")
            self.assertEqual(dead["count"], 1)
            self.assertEqual(dead["items"][0]["job_id"], "job-dl1")

            locks = await cleaner_router.list_target_locks(_="admin")
            self.assertEqual(locks["count"], 1)
            self.assertEqual(locks["items"][0]["lock_key"], "chat:777")


if __name__ == "__main__":
    unittest.main()
