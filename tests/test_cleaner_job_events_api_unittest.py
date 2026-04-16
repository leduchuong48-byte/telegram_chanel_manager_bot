from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner as cleaner_router
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime


class CleanerJobEventsApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_job_events_endpoint_returns_latest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "bot.db"
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")

            db = Database(db_path)
            db.create_job(job_id="job-e1", chat_id=-1001, task_type="batch_delete", payload_json="{}", priority=1)
            db.append_job_event(job_id="job-e1", event_type="created")
            db.append_job_event(job_id="job-e1", event_type="started")
            db.append_job_event(job_id="job-e1", event_type="completed")

            runtime = PipelineRuntime(db, worker_count=1)
            cleaner_router.set_config_manager(ConfigManager(cfg))
            cleaner_router.set_pipeline_runtime(runtime)

            payload = await cleaner_router.get_cleaner_job_events(job_id="job-e1", limit=20, _="admin")
            self.assertEqual(payload["job_id"], "job-e1")
            self.assertGreaterEqual(payload["count"], 3)
            events = payload["events"]
            self.assertEqual(events[0]["event_type"], "completed")
            self.assertEqual(events[-1]["event_type"], "created")


if __name__ == "__main__":
    unittest.main()
