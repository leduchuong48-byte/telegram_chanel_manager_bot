from __future__ import annotations

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
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class _FakeClient:
    def __init__(self) -> None:
        self.deleted: list[list[int]] = []

    async def iter_messages(self, _entity: object, *, limit: int):
        for idx in range(1, limit + 1):
            yield _FakeMessage(idx)

    async def delete_messages(self, _entity: object, ids: list[int]) -> None:
        self.deleted.append(list(ids))


class _FakeClientContext:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> _FakeClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class CleanerBatchExecutorTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_batch_delete_executor_deletes_messages_via_runtime(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = False

        spec = JobSpec(
            job_id="job-batch-1",
            chat_id=0,
            job_type=JobType.BATCH_DELETE,
            payload={"count": 3, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        self.assertEqual(self.fake_client.deleted, [[1, 2, 3]])
        row = self.db.get_job("job-batch-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")

    async def test_batch_delete_respects_dry_run(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = True

        spec = JobSpec(
            job_id="job-batch-2",
            chat_id=-1001234567890,
            job_type=JobType.BATCH_DELETE,
            payload={"count": 3, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)

        await self.runtime.start()
        try:
            await self.runtime.drain_once()
        finally:
            await self.runtime.shutdown()

        self.assertEqual(self.fake_client.deleted, [])
        row = self.db.get_job("job-batch-2")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["scanned"], 3)
        self.assertEqual(row["matched"], 3)
        self.assertEqual(row["acted"], 0)


if __name__ == "__main__":
    unittest.main()
