from __future__ import annotations

import unittest

from tg_media_dedupe_bot.task_models import JobProgress, JobSpec, JobStatus, JobType


class TaskModelsTest(unittest.TestCase):
    def test_job_spec_defaults(self) -> None:
        spec = JobSpec(job_id="job-1", chat_id=-1001, job_type=JobType.SCAN, payload={"limit": 100})
        self.assertEqual(spec.status, JobStatus.PENDING)
        self.assertEqual(spec.priority, 0)
        self.assertEqual(spec.payload["limit"], 100)

    def test_progress_snapshot(self) -> None:
        progress = JobProgress(scanned=10, matched=3, acted=2, failed=1)
        self.assertEqual(progress.to_dict(), {"scanned": 10, "matched": 3, "acted": 2, "failed": 1})


if __name__ == "__main__":
    unittest.main()
