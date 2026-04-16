from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class CleanerPageTest(unittest.TestCase):
    def test_cleaner_submit_redirects_to_task_center_detail(self) -> None:
        template = (ROOT / 'app/templates/cleaner.html').read_text(encoding='utf-8')
        self.assertIn('/task_center?task=', template)
        self.assertIn('任务已创建，正在跳转任务中心', template)


if __name__ == '__main__':
    unittest.main()
