from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class TaskCenterPageTest(unittest.TestCase):
    def test_main_has_task_center_route(self) -> None:
        main_py = (ROOT / 'app/main.py').read_text(encoding='utf-8')
        self.assertIn('@app.get("/task_center"', main_py)
        self.assertIn('"task_center.html"', main_py)

    def test_base_nav_has_task_center_entry(self) -> None:
        base_html = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('href="/task_center"', base_html)
        self.assertIn('任务日志', base_html)

    def test_task_center_template_has_jobs_window(self) -> None:
        template = (ROOT / 'app/templates/task_center.html').read_text(encoding='utf-8')
        self.assertIn('任务结果摘要', template)
        self.assertIn('任务列表', template)
        self.assertIn('结果摘要', template)
        self.assertIn('是否开始', template)
        self.assertIn('是否完成', template)
        self.assertIn('是否有实际处理', template)
        self.assertIn('下一步动作', template)
        self.assertIn('/api/cleaner/jobs', template)
        self.assertIn('/api/cleaner/monitoring', template)
        self.assertIn('取消任务', template)
        self.assertIn('任务详情', template)
        self.assertIn('关键时间线', template)
        self.assertIn('原始事件（证据）', template)
        self.assertIn('related_links', template)
        self.assertIn('/api/cleaner/jobs/${encodeURIComponent', template)
        self.assertIn('URLSearchParams(window.location.search)', template)
        self.assertIn("params.get('task')", template)
        self.assertIn('outcome_summary', template)
        self.assertIn('operator_summary', template)
        self.assertIn('business_result', template)
        self.assertIn('let currentJobId =', template)
        self.assertIn('if (currentJobId) {', template)
        self.assertIn('await loadJobDetail(currentJobId);', template)
        self.assertIn('let detailRequestSeq = 0;', template)
        self.assertIn('let lastRenderedJobId =', template)
        self.assertIn('let hasRenderedDetail = false;', template)
        self.assertIn('const requestSeq = ++detailRequestSeq;', template)
        self.assertIn('if (requestSeq !== detailRequestSeq) return;', template)


if __name__ == '__main__':
    unittest.main()
