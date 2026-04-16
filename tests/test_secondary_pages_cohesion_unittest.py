from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class SecondaryPagesCohesionTest(unittest.TestCase):
    def test_cleaner_page_has_diagnostic_flow_and_task_linkage(self) -> None:
        page = (ROOT / 'app/templates/cleaner.html').read_text(encoding='utf-8')
        self.assertIn('状态 -> 证据 -> 动作', page)
        self.assertIn('进入任务中心跟踪', page)
        self.assertIn('关联诊断', page)

    def test_logs_page_has_diagnostic_positioning_and_context_filters(self) -> None:
        page = (ROOT / 'app/templates/logs.html').read_text(encoding='utf-8')
        self.assertIn('诊断日志流', page)
        self.assertIn('taskFilter', page)
        self.assertIn('chatFilter', page)

    def test_telegram_controllers_page_has_identity_boundary_copy(self) -> None:
        page = (ROOT / 'app/templates/telegram_controllers.html').read_text(encoding='utf-8')
        self.assertIn('身份边界', page)
        self.assertIn('Web 登录账号', page)
        self.assertIn('Telegram 控制身份', page)


if __name__ == '__main__':
    unittest.main()
