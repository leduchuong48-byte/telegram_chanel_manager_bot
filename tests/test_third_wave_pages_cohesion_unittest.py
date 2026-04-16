from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class ThirdWavePagesCohesionTest(unittest.TestCase):
    def test_users_page_has_boundary_and_followup_navigation_copy(self) -> None:
        page = (ROOT / 'app/templates/users.html').read_text(encoding='utf-8')
        self.assertIn('配置边界', page)
        self.assertIn('账号变更后建议动作', page)
        self.assertIn('进入任务中心', page)

    def test_bot_settings_page_has_risk_and_execution_chain_copy(self) -> None:
        page = (ROOT / 'app/templates/bot_settings.html').read_text(encoding='utf-8')
        self.assertIn('执行链路影响', page)
        self.assertIn('风险提示', page)
        self.assertIn('关联诊断', page)

    def test_tools_page_has_task_tracking_language(self) -> None:
        page = (ROOT / 'app/templates/tools.html').read_text(encoding='utf-8')
        self.assertIn('状态 -> 证据 -> 动作', page)
        self.assertIn('任务中心追踪', page)
        self.assertIn('可见性诊断', page)


if __name__ == '__main__':
    unittest.main()
