from __future__ import annotations

import asyncio
import unittest

from tg_media_dedupe_bot.telethon_scan import ScanResult, _raise_if_stop_requested as scan_raise_if_stop_requested, build_scan_progress_snapshot
from tg_media_dedupe_bot.telethon_tags import TagScanProgress, TagScanResult, _raise_if_stop_requested as tag_raise_if_stop_requested, build_tag_progress_snapshot


class ScanTagProgressSnapshotTest(unittest.TestCase):
    def test_build_scan_progress_snapshot(self) -> None:
        result = ScanResult(scanned=12, decided_delete=5, deleted=4, failed=1)
        snapshot = build_scan_progress_snapshot(result, status="running")
        self.assertEqual(
            snapshot,
            {
                "status": "running",
                "scanned": 12,
                "matched": 5,
                "acted": 4,
                "failed": 1,
            },
        )

    def test_build_tag_progress_snapshot_from_progress(self) -> None:
        progress = TagScanProgress(scanned=20, unique_tags=7, total_tags=15)
        snapshot = build_tag_progress_snapshot(progress, status="running")
        self.assertEqual(
            snapshot,
            {
                "status": "running",
                "scanned": 20,
                "unique_tags": 7,
                "total_tags": 15,
            },
        )

    def test_build_tag_progress_snapshot_from_result(self) -> None:
        result = TagScanResult(scanned=30, tag_counts={"foo": 2, "bar": 1}, total_tags=3)
        snapshot = build_tag_progress_snapshot(result, status="completed")
        self.assertEqual(
            snapshot,
            {
                "status": "completed",
                "scanned": 30,
                "unique_tags": 2,
                "total_tags": 3,
            },
        )

    def test_scan_raise_if_stop_requested_raises_cancelled(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            scan_raise_if_stop_requested(lambda: True)

    def test_tag_raise_if_stop_requested_raises_cancelled(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            tag_raise_if_stop_requested(lambda: True)


if __name__ == "__main__":
    unittest.main()
