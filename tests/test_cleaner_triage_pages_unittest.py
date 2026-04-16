from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class CleanerTriagePagesTest(unittest.TestCase):
    def test_main_has_review_and_dead_letter_routes(self) -> None:
        main_py = (ROOT / 'app/main.py').read_text(encoding='utf-8')
        self.assertIn('@app.get("/review_queue"', main_py)
        self.assertIn('"review_queue.html"', main_py)
        self.assertIn('@app.get("/dead_letters"', main_py)
        self.assertIn('"dead_letters.html"', main_py)

    def test_base_nav_has_triage_entries(self) -> None:
        base_html = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('href="/review_queue"', base_html)
        self.assertIn('复核队列', base_html)
        self.assertIn('href="/dead_letters"', base_html)
        self.assertIn('死信队列', base_html)

    def test_triage_templates_have_core_markers(self) -> None:
        review_tpl = (ROOT / 'app/templates/review_queue.html').read_text(encoding='utf-8')
        dead_tpl = (ROOT / 'app/templates/dead_letters.html').read_text(encoding='utf-8')
        self.assertIn('/api/cleaner/review_queue', review_tpl)
        self.assertIn('需要复核任务', review_tpl)
        self.assertIn('结果摘要', review_tpl)
        self.assertIn('优先动作', review_tpl)
        self.assertIn('task_center', review_tpl)
        self.assertIn('next_action_summary', review_tpl)
        self.assertIn('/api/cleaner/dead_letters', dead_tpl)
        self.assertIn('死信任务', dead_tpl)
        self.assertIn('终止结论', dead_tpl)
        self.assertIn('建议动作', dead_tpl)
        self.assertIn('task_center', dead_tpl)
        self.assertIn('next_action_summary', dead_tpl)


if __name__ == '__main__':
    unittest.main()
