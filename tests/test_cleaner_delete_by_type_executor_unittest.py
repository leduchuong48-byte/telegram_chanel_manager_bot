from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import cleaner
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType


class _FakeMessage:
    def __init__(self, message_id: int, *, text: bool = False, photo: bool = False) -> None:
        self.id = message_id
        self.media = None if text else object()
        self.message = "hello" if text else ""
        self.photo = object() if photo else None
        self.video = None
        self.sticker = None


class _FakeClient:
    def __init__(self) -> None:
        self.deleted: list[list[int]] = []
        self.messages = [
            _FakeMessage(1, text=True),
            _FakeMessage(2, photo=True),
            _FakeMessage(3, text=True),
        ]

    async def iter_messages(self, _entity: object, *, limit: int):
        for msg in self.messages[:limit]:
            yield msg

    async def delete_messages(self, _entity: object, ids: list[int]) -> None:
        self.deleted.append(list(ids))


class _PartialFailClient(_FakeClient):
    async def delete_messages(self, _entity: object, ids: list[int]) -> None:
        self.deleted.append(list(ids))
        raise RuntimeError("partial delete failed")


class _FloodWaitError(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"flood wait {seconds}")
        self.seconds = seconds


class _FloodWaitClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def delete_messages(self, _entity: object, ids: list[int]) -> None:
        self.calls += 1
        self.deleted.append(list(ids))
        if self.calls == 1:
            raise _FloodWaitError(0)


class _FakeClientContext:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> _FakeClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class CleanerDeleteByTypeExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.config_path = root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "web_admin": {"secret_key": "test-secret"},
                    "bot": {"target_chat_ids": ["-1001234567890"]},
                    "database": {"path": str(root / "bot.db")},
                }
            ),
            encoding="utf-8",
        )
        self.manager = ConfigManager(self.config_path)
        self.db = Database(root / "bot.db")
        self.runtime = PipelineRuntime(self.db)
        cleaner.set_config_manager(self.manager)
        cleaner.set_pipeline_runtime(self.runtime)
        cleaner.register_runtime_executors(self.runtime)

        self.fake_client = _FakeClient()
        self._old_open_web_client = cleaner.open_web_client
        self._old_resolve_targets = cleaner._resolve_targets
        cleaner.open_web_client = lambda *args, **kwargs: _FakeClientContext(self.fake_client)

        async def _fake_resolve_targets(_client, _bot_config, *, requested_target=None):
            token = requested_target or "-1001234567890"
            return [(token, object())]

        cleaner._resolve_targets = _fake_resolve_targets

    async def asyncTearDown(self) -> None:
        cleaner.open_web_client = self._old_open_web_client
        cleaner._resolve_targets = self._old_resolve_targets
        cleaner.set_pipeline_runtime(None)
        self.db.close()
        self.tmpdir.cleanup()

    async def test_delete_by_type_executor_only_deletes_matching_messages(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = False

        spec = JobSpec(
            job_id="job-delete-type-1",
            chat_id=0,
            job_type=JobType.DELETE_BY_TYPE,
            payload={"types": ["text"], "limit": 10, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        self.assertEqual(self.fake_client.deleted, [[1, 3]])
        row = self.db.get_job("job-delete-type-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")

    async def test_delete_by_type_records_failed_progress_when_delete_raises(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = False

        failing_client = _PartialFailClient()
        cleaner.open_web_client = lambda *args, **kwargs: _FakeClientContext(failing_client)

        spec = JobSpec(
            job_id="job-delete-type-2",
            chat_id=0,
            job_type=JobType.DELETE_BY_TYPE,
            payload={"types": ["text"], "limit": 10, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job("job-delete-type-2")
                if row is not None and row["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("delete-by-type failure case did not finish in time")
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-delete-type-2")
        self.assertEqual(row["matched"], 2)
        self.assertEqual(row["acted"], 0)
        self.assertEqual(row["failed"], 2)

    async def test_delete_by_type_retries_after_flood_wait(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = False

        flood_client = _FloodWaitClient()
        cleaner.open_web_client = lambda *args, **kwargs: _FakeClientContext(flood_client)
        pauses: list[tuple[int, float]] = []
        original_pause_chat = self.runtime.pause_chat

        def recording_pause_chat(chat_id: int, seconds: float) -> None:
            pauses.append((chat_id, seconds))
            original_pause_chat(chat_id, seconds)

        self.runtime.pause_chat = recording_pause_chat  # type: ignore[method-assign]

        spec = JobSpec(
            job_id="job-delete-type-3",
            chat_id=-1001234567890,
            job_type=JobType.DELETE_BY_TYPE,
            payload={"types": ["text"], "limit": 10, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job("job-delete-type-3")
                if row is not None and row["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("delete-by-type flood wait case did not finish in time")
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-delete-type-3")
        self.assertEqual(row["matched"], 2)
        self.assertEqual(row["acted"], 2)
        self.assertEqual(row["failed"], 0)
        self.assertEqual(flood_client.calls, 2)
        self.assertEqual(pauses, [(-1001234567890, 0)])

    async def test_delete_by_type_respects_dry_run(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = True

        spec = JobSpec(
            job_id="job-delete-type-4",
            chat_id=-1001234567890,
            job_type=JobType.DELETE_BY_TYPE,
            payload={"types": ["text"], "limit": 10, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job("job-delete-type-4")
                if row is not None and row["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("dry-run delete-by-type job did not finish in time")
        finally:
            await self.runtime.shutdown()

        row = self.db.get_job("job-delete-type-4")
        self.assertEqual(self.fake_client.deleted, [])
        self.assertEqual(row["matched"], 2)
        self.assertEqual(row["acted"], 0)
        self.assertEqual(row["failed"], 0)


if __name__ == "__main__":
    unittest.main()
