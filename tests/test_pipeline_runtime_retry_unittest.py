from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType


class PipelineRuntimeRetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "bot.db"
        self.db = Database(self.db_path)
        self.runtime = PipelineRuntime(self.db)

    async def asyncTearDown(self) -> None:
        await self.runtime.shutdown()
        self.db.close()
        self.tmpdir.cleanup()

    async def test_retries_timeout_error_once_then_completes(self) -> None:
        attempts = 0

        async def executor(_spec: JobSpec) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("temporary timeout")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.submit(JobSpec(job_id="retry-1", chat_id=1, job_type=JobType.BATCH_DELETE, payload={}))
        await self.runtime.start()

        for _ in range(100):
            row = self.db.get_job("retry-1")
            if row is not None and row["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

        row = self.db.get_job("retry-1")
        self.assertEqual(attempts, 2)
        self.assertEqual(row["status"], "completed")

    async def test_does_not_retry_permission_error(self) -> None:
        attempts = 0

        async def executor(_spec: JobSpec) -> None:
            nonlocal attempts
            attempts += 1
            raise PermissionError("forbidden")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.submit(JobSpec(job_id="retry-2", chat_id=1, job_type=JobType.BATCH_DELETE, payload={}))
        await self.runtime.start()

        for _ in range(100):
            row = self.db.get_job("retry-2")
            if row is not None and row["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)

        row = self.db.get_job("retry-2")
        self.assertEqual(attempts, 1)
        self.assertEqual(row["status"], "failed")
        self.assertIn("forbidden", str(row["last_error"]))


if __name__ == "__main__":
    unittest.main()
