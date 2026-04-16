from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database


class CleanerControlPlaneSchemaTest(unittest.TestCase):
    def test_jobs_table_contains_control_plane_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                conn: sqlite3.Connection = db._conn
                rows = conn.execute("PRAGMA table_info(jobs)").fetchall()
                cols = {str(r[1]) for r in rows}
                self.assertIn("attempt_count", cols)
                self.assertIn("max_attempts", cols)
                self.assertIn("next_run_at", cols)
                self.assertIn("worker_id", cols)
                self.assertIn("lease_expires_at", cols)
                self.assertIn("last_heartbeat_at", cols)
                self.assertIn("terminal_reason", cols)
                self.assertIn("retryable_class", cols)
                self.assertIn("submitted_by", cols)
                self.assertIn("session_snapshot_json", cols)
                self.assertIn("target_snapshot_json", cols)
                self.assertIn("policy_snapshot_json", cols)
            finally:
                db.close()

    def test_control_plane_tables_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bot.db")
            try:
                conn: sqlite3.Connection = db._conn
                rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                names = {str(r[0]) for r in rows}
                self.assertIn("job_events", names)
                self.assertIn("target_locks", names)
                self.assertIn("dead_letter_actions", names)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
