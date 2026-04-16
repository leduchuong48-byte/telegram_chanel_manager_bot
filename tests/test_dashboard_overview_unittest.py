from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class DashboardOverviewTest(unittest.TestCase):
    def test_dashboard_has_unified_status_cards(self) -> None:
        dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
        self.assertIn('系统可执行性', dashboard)
        self.assertIn('执行身份', dashboard)
        self.assertIn('控制权限', dashboard)
        self.assertIn('运行模式', dashboard)
        self.assertIn('任务概览', dashboard)
        self.assertIn('最近异常', dashboard)
        self.assertIn('当前操作主轴', dashboard)
        self.assertIn('建议下一步', dashboard)

    def test_dashboard_uses_existing_status_apis(self) -> None:
        dashboard = (ROOT / 'app/templates/dashboard.html').read_text(encoding='utf-8')
        self.assertIn('/api/cleaner/monitoring', dashboard)
        self.assertIn('/api/cleaner/jobs', dashboard)
        self.assertIn('/api/session/status', dashboard)
        self.assertIn('/api/telegram-controllers', dashboard)


if __name__ == '__main__':
    unittest.main()
