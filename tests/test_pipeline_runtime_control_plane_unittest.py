from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType


class PipelineRuntimeControlPlaneTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmpdir.name) / "bot.db")
        self.runtime = PipelineRuntime(self.db)

    async def asyncTearDown(self) -> None:
        await self.runtime.shutdown()
        self.db.close()
        self.tmpdir.cleanup()

    async def test_non_retryable_error_moves_to_dead_letter(self) -> None:
        async def executor(_spec: JobSpec) -> None:
            raise HTTPException(status_code=400, detail="unable to resolve target")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.submit(JobSpec(job_id="cp-1", chat_id=1, job_type=JobType.BATCH_DELETE, payload={}))
        await self.runtime.start()

        for _ in range(120):
            row = self.db.get_job("cp-1")
            if row is not None and str(row["status"]) in {"dead_letter", "failed_permanent", "failed"}:
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("job did not transition to dead-letter/permanent/failed")

        row = self.db.get_job("cp-1")
        self.assertIn(str(row["status"]), {"dead_letter", "failed_permanent", "failed"})

    async def test_retry_wait_state_is_used_for_retryable_errors(self) -> None:
        attempts = 0

        async def executor(_spec: JobSpec) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("transient io error")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.submit(JobSpec(job_id="cp-2", chat_id=2, job_type=JobType.BATCH_DELETE, payload={}))
        await self.runtime.start()

        saw_retry_wait = False
        for _ in range(220):
            row = self.db.get_job("cp-2")
            if row is not None and str(row["status"]) == "retry_wait":
                saw_retry_wait = True
            if row is not None and str(row["status"]) == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("retryable job did not complete")

        self.assertTrue(saw_retry_wait)

    async def test_runtime_persists_target_lock_records(self) -> None:
        async def executor(_spec: JobSpec) -> None:
            await asyncio.sleep(0.03)

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.submit(JobSpec(job_id="cp-3", chat_id=333, job_type=JobType.BATCH_DELETE, payload={}))
        await self.runtime.start()

        saw_lock = False
        for _ in range(120):
            rows = self.db._conn.execute("SELECT lock_key FROM target_locks WHERE lock_key=?", ("chat:333",)).fetchall()
            if rows:
                saw_lock = True
                break
            await asyncio.sleep(0.01)

        for _ in range(200):
            row = self.db.get_job("cp-3")
            if row is not None and str(row["status"]) == "completed":
                break
            await asyncio.sleep(0.01)

        left = self.db._conn.execute("SELECT lock_key FROM target_locks WHERE lock_key=?", ("chat:333",)).fetchall()
        self.assertTrue(saw_lock)
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
