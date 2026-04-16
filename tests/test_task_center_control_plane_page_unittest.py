from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class TaskCenterControlPlanePageTest(unittest.TestCase):
    def test_task_center_has_retry_dead_letter_filters_and_event_fetch(self) -> None:
        template = (ROOT / 'app/templates/task_center.html').read_text(encoding='utf-8')
        self.assertIn('option value="retry_wait"', template)
        self.assertIn('option value="dead_letter"', template)
        self.assertIn('/api/cleaner/jobs/${encodeURIComponent(normalizedJobId)}/events', template)
        self.assertIn('原始事件（证据）', template)
        self.assertIn('<details>', template)


if __name__ == '__main__':
    unittest.main()
