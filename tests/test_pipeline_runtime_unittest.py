from __future__ import annotations

import asyncio
import tempfile
import time
import unittest

from fastapi import HTTPException
from pathlib import Path

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType


class PipelineRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "bot.db"
        self.db = Database(self.db_path)
        self.runtime = PipelineRuntime(self.db)

    async def asyncTearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    async def test_submit_job_persists_pending_state(self) -> None:
        spec = JobSpec(job_id="job-1", chat_id=-1001, job_type=JobType.SCAN, payload={"limit": 50})
        created = await self.runtime.submit(spec)
        self.assertEqual(created.job_id, "job-1")
        row = self.db.get_job("job-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")

    async def test_start_job_marks_running(self) -> None:
        spec = JobSpec(job_id="job-2", chat_id=-1002, job_type=JobType.DELETE_BY_TYPE, payload={"types": ["text"]})
        await self.runtime.submit(spec)
        started = await self.runtime.mark_running("job-2")
        self.assertEqual(started.status.value, "running")
        row = self.db.get_job("job-2")
        self.assertEqual(row["status"], "running")

    async def test_worker_processes_batch_delete_to_completed(self) -> None:
        spec = JobSpec(job_id="job-3", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 10})
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-3")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")
        self.assertGreater(int(row["started_at"]), 0)
        self.assertGreater(int(row["finished_at"]), 0)

    async def test_registered_executor_is_called_with_job_spec(self) -> None:
        seen: dict[str, object] = {}

        async def executor(spec: JobSpec) -> None:
            seen["job_id"] = spec.job_id
            seen["job_type"] = spec.job_type.value
            seen["payload"] = spec.payload

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        spec = JobSpec(job_id="job-4", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 7})
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        self.assertEqual(seen["job_id"], "job-4")
        self.assertEqual(seen["job_type"], "batch_delete")
        self.assertEqual(seen["payload"], {"count": 7})
        row = self.db.get_job("job-4")
        self.assertEqual(row["status"], "completed")

    async def test_executor_failure_marks_job_failed(self) -> None:
        async def executor(_spec: JobSpec) -> None:
            raise RuntimeError("boom")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        spec = JobSpec(job_id="job-5", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 3})
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-5")
        self.assertEqual(row["status"], "failed")
        self.assertIn("boom", str(row["last_error"]))

    async def test_http_exception_preserves_detail_in_last_error(self) -> None:
        async def executor(_spec: JobSpec) -> None:
            raise HTTPException(status_code=400, detail="unable to resolve target")

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        spec = JobSpec(job_id="job-5h", chat_id=0, job_type=JobType.BATCH_DELETE, payload={})
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job("job-5h")
                if row is not None and row["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("http exception job did not finish in time")
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-5h")
        self.assertEqual(row["status"], "failed")
        self.assertIn("unable to resolve target", str(row["last_error"]))


    async def test_empty_exception_message_uses_exception_type_for_last_error(self) -> None:
        class SilentError(Exception):
            pass

        async def executor(_spec: JobSpec) -> None:
            raise SilentError()

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        spec = JobSpec(job_id="job-5b", chat_id=0, job_type=JobType.BATCH_DELETE, payload={})
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job("job-5b")
                if row is not None and row["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("silent failure job did not finish in time")
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-5b")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["last_error"], "SilentError")

    async def test_background_worker_processes_submitted_jobs(self) -> None:
        async def executor(_spec: JobSpec) -> None:
            await asyncio.sleep(0)

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        await self.runtime.start()
        try:
            spec = JobSpec(job_id="job-6", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 2})
            await self.runtime.submit(spec)

            for _ in range(50):
                row = self.db.get_job("job-6")
                if row is not None and row["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("background worker did not complete job-6")
        finally:
            await self.runtime.shutdown()

    async def test_shutdown_stops_background_worker_task(self) -> None:
        await self.runtime.start()
        self.assertTrue(self.runtime.is_running)
        await self.runtime.shutdown()
        self.assertFalse(self.runtime.is_running)

    async def test_cancel_job_marks_pending_job_cancelled(self) -> None:
        spec = JobSpec(job_id="job-7", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 2})
        await self.runtime.submit(spec)

        cancelled = await self.runtime.cancel("job-7")
        self.assertTrue(cancelled)
        row = self.db.get_job("job-7")
        self.assertEqual(row["status"], "cancelled")

    async def test_cancelled_job_is_not_processed_by_worker(self) -> None:
        called = False

        async def executor(_spec: JobSpec) -> None:
            nonlocal called
            called = True

        self.runtime.register_executor(JobType.BATCH_DELETE, executor)
        spec = JobSpec(job_id="job-8", chat_id=0, job_type=JobType.BATCH_DELETE, payload={"count": 1})
        await self.runtime.submit(spec)
        await self.runtime.cancel("job-8")

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        self.assertFalse(called)
        row = self.db.get_job("job-8")
        self.assertEqual(row["status"], "cancelled")

    async def test_jobs_same_chat_do_not_overlap(self) -> None:
        runtime = PipelineRuntime(self.db, worker_count=2)
        order: list[str] = []
        overlap_detected = False
        active = 0

        async def executor(spec: JobSpec) -> None:
            nonlocal active, overlap_detected
            active += 1
            if active > 1:
                overlap_detected = True
            order.append(f"start:{spec.job_id}")
            await asyncio.sleep(0.05)
            order.append(f"end:{spec.job_id}")
            active -= 1

        runtime.register_executor(JobType.BATCH_DELETE, executor)
        await runtime.submit(JobSpec(job_id="job-9", chat_id=123, job_type=JobType.BATCH_DELETE, payload={}))
        await runtime.submit(JobSpec(job_id="job-10", chat_id=123, job_type=JobType.BATCH_DELETE, payload={}))

        await runtime.start()
        try:
            for _ in range(100):
                a = self.db.get_job("job-9")
                b = self.db.get_job("job-10")
                if a is not None and b is not None and a["status"] == "completed" and b["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("same-chat jobs did not finish in time")
        finally:
            await runtime.shutdown()

        self.assertFalse(overlap_detected)
        self.assertEqual(order, ["start:job-9", "end:job-9", "start:job-10", "end:job-10"])

    async def test_jobs_different_chats_can_overlap_up_to_worker_limit(self) -> None:
        runtime = PipelineRuntime(self.db, worker_count=2)
        starts: list[float] = []

        async def executor(_spec: JobSpec) -> None:
            starts.append(time.monotonic())
            await asyncio.sleep(0.05)

        runtime.register_executor(JobType.BATCH_DELETE, executor)
        await runtime.submit(JobSpec(job_id="job-11", chat_id=111, job_type=JobType.BATCH_DELETE, payload={}))
        await runtime.submit(JobSpec(job_id="job-12", chat_id=222, job_type=JobType.BATCH_DELETE, payload={}))

        await runtime.start()
        try:
            for _ in range(100):
                a = self.db.get_job("job-11")
                b = self.db.get_job("job-12")
                if a is not None and b is not None and a["status"] == "completed" and b["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("different-chat jobs did not finish in time")
        finally:
            await runtime.shutdown()

        self.assertEqual(len(starts), 2)
        self.assertLess(abs(starts[0] - starts[1]), 0.08)

    async def test_paused_chat_delays_next_same_chat_job(self) -> None:
        runtime = PipelineRuntime(self.db, worker_count=2)
        starts: dict[str, float] = {}

        async def executor(spec: JobSpec) -> None:
            starts[spec.job_id] = time.monotonic()
            if spec.job_id == "job-13":
                runtime.pause_chat(spec.chat_id, 0.08)
            await asyncio.sleep(0)

        runtime.register_executor(JobType.BATCH_DELETE, executor)
        await runtime.submit(JobSpec(job_id="job-13", chat_id=333, job_type=JobType.BATCH_DELETE, payload={}))
        await runtime.submit(JobSpec(job_id="job-14", chat_id=333, job_type=JobType.BATCH_DELETE, payload={}))

        await runtime.start()
        try:
            for _ in range(120):
                a = self.db.get_job("job-13")
                b = self.db.get_job("job-14")
                if a is not None and b is not None and a["status"] == "completed" and b["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("paused same-chat jobs did not finish in time")
        finally:
            await runtime.shutdown()

        self.assertIn("job-13", starts)
        self.assertIn("job-14", starts)
        self.assertGreaterEqual(starts["job-14"] - starts["job-13"], 0.07)


if __name__ == "__main__":
    unittest.main()
