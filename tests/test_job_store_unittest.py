from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database


class JobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "bot.db"
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def test_job_tables_are_created(self) -> None:
        tables = {
            row[0]
            for row in self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("jobs", tables)
        self.assertIn("job_checkpoints", tables)
        self.assertIn("job_actions", tables)

    def test_create_and_update_job_lifecycle(self) -> None:
        self.db.create_job(
            job_id="job-1",
            chat_id=-100123,
            task_type="scan_delete",
            payload_json='{"limit": 100}',
            priority=5,
        )

        job = self.db.get_job("job-1")
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["chat_id"], -100123)
        self.assertEqual(job["task_type"], "scan_delete")

        self.db.update_job_status("job-1", "running")
        running = self.db.get_job("job-1")
        self.assertEqual(running["status"], "running")
        self.assertGreater(int(running["started_at"]), 0)

        self.db.update_job_status("job-1", "completed")
        finished = self.db.get_job("job-1")
        self.assertEqual(finished["status"], "completed")
        self.assertGreater(int(finished["finished_at"]), 0)

    def test_upsert_checkpoint(self) -> None:
        self.db.create_job(
            job_id="job-2",
            chat_id=-100456,
            task_type="scan",
            payload_json="{}",
            priority=1,
        )
        self.db.upsert_job_checkpoint(
            job_id="job-2",
            stage="scan",
            cursor_json='{"offset_id": 1000}',
        )
        self.db.upsert_job_checkpoint(
            job_id="job-2",
            stage="scan",
            cursor_json='{"offset_id": 900}',
        )

        checkpoint = self.db.get_job_checkpoint("job-2", "scan")
        self.assertEqual(checkpoint["cursor_json"], '{"offset_id": 900}')

    def test_job_action_is_idempotent(self) -> None:
        self.db.create_job(
            job_id="job-3",
            chat_id=-100789,
            task_type="delete_by_type",
            payload_json="{}",
            priority=1,
        )
        self.db.record_job_action(
            idempotency_key="-100789:42:delete:text:v1",
            job_id="job-3",
            chat_id=-100789,
            message_id=42,
            action="delete",
            status="pending",
            error=None,
        )
        self.db.record_job_action(
            idempotency_key="-100789:42:delete:text:v1",
            job_id="job-3",
            chat_id=-100789,
            message_id=42,
            action="delete",
            status="success",
            error=None,
        )

        action = self.db.get_job_action("-100789:42:delete:text:v1")
        self.assertEqual(action["status"], "success")

        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM job_actions WHERE idempotency_key=?",
            ("-100789:42:delete:text:v1",),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_update_job_progress_persists_counters(self) -> None:
        self.db.create_job(
            job_id="job-4",
            chat_id=-100999,
            task_type="batch_delete",
            payload_json="{}",
            priority=1,
        )

        self.db.update_job_progress(
            "job-4",
            scanned=12,
            matched=8,
            acted=6,
            failed=1,
        )

        job = self.db.get_job("job-4")
        self.assertEqual(job["scanned"], 12)
        self.assertEqual(job["matched"], 8)
        self.assertEqual(job["acted"], 6)
        self.assertEqual(job["failed"], 1)


if __name__ == "__main__":
    unittest.main()
