from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class HolisticNavigationTest(unittest.TestCase):
    def test_base_has_grouped_navigation_sections(self) -> None:
        base_html = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('总览', base_html)
        self.assertIn('执行与任务', base_html)
        self.assertIn('Telegram 运行面', base_html)
        self.assertIn('系统配置', base_html)
        self.assertIn('诊断与审计', base_html)
        self.assertIn('运营态势控制台', base_html)

    def test_base_has_overview_route_entry(self) -> None:
        base_html = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('href="/"', base_html)
        self.assertIn('控制台总览', base_html)


if __name__ == '__main__':
    unittest.main()
