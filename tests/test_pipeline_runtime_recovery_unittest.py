from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType


class PipelineRuntimeRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_requeues_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            db.create_job(
                job_id="job-r1",
                chat_id=101,
                task_type=JobType.BATCH_DELETE.value,
                payload_json="{}",
                priority=0,
            )
            db.update_job_status("job-r1", "running")

            runtime = PipelineRuntime(db, worker_count=1)

            executed: list[str] = []

            async def _executor(spec: JobSpec) -> None:
                executed.append(spec.job_id)

            runtime.register_executor(JobType.BATCH_DELETE, _executor)
            await runtime.start()
            try:
                for _ in range(100):
                    row = db.get_job("job-r1")
                    if row is not None and str(row["status"]) == "completed":
                        break
                    await asyncio.sleep(0.01)
                else:
                    self.fail("running job was not re-processed")
            finally:
                await runtime.shutdown()
                db.close()

            self.assertEqual(executed, ["job-r1"])


if __name__ == "__main__":
    unittest.main()
