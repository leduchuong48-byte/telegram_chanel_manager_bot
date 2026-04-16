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
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.media = None
        self.message = f"msg-{message_id}"
        self.photo = None
        self.video = None
        self.sticker = None


class _SlowClient:
    def __init__(self, runtime: PipelineRuntime, job_id: str) -> None:
        self.runtime = runtime
        self.job_id = job_id
        self.deleted: list[list[int]] = []

    async def iter_messages(self, _entity: object, *, limit: int):
        for idx in range(1, limit + 1):
            if idx == 4:
                await self.runtime.cancel(self.job_id)
            await asyncio.sleep(0)
            yield _FakeMessage(idx)

    async def delete_messages(self, _entity: object, ids: list[int]) -> None:
        self.deleted.append(list(ids))


class _SlowClientContext:
    def __init__(self, client: _SlowClient) -> None:
        self.client = client

    async def __aenter__(self) -> _SlowClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class CleanerInflightCancelTest(unittest.IsolatedAsyncioTestCase):
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

        self._old_open_web_client = cleaner.open_web_client
        self._old_resolve_targets = cleaner._resolve_targets

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

    async def test_delete_by_type_stops_mid_run_after_cancel(self) -> None:
        config = self.manager.get_config()
        config["bot"]["dry_run"] = False

        job_id = "job-inflight-cancel-1"
        client = _SlowClient(self.runtime, job_id)
        cleaner.open_web_client = lambda *args, **kwargs: _SlowClientContext(client)

        spec = JobSpec(
            job_id=job_id,
            chat_id=0,
            job_type=JobType.DELETE_BY_TYPE,
            payload={"types": ["text"], "limit": 10, "target": "-1001234567890"},
        )
        await self.runtime.submit(spec)
        await self.runtime.start()
        try:
            for _ in range(100):
                row = self.db.get_job(job_id)
                if row is not None and row["status"] == "cancelled":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("job was not cancelled in time")
        finally:
            await self.runtime.shutdown()

        self.assertEqual(client.deleted, [[1, 2, 3]])
        row = self.db.get_job(job_id)
        self.assertEqual(row["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
