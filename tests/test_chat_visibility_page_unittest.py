from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class ChatVisibilityPageTest(unittest.TestCase):
    def test_main_has_chat_visibility_route(self) -> None:
        main_py = (ROOT / 'app/main.py').read_text(encoding='utf-8')
        self.assertIn('@app.get("/chat_visibility"', main_py)
        self.assertIn('"chat_visibility.html"', main_py)

    def test_template_uses_effective_state_api(self) -> None:
        template = (ROOT / 'app/templates/chat_visibility.html').read_text(encoding='utf-8')
        self.assertIn('/api/chat_effective_state/chats', template)
        self.assertIn('/api/chat_effective_state/events', template)
        self.assertIn('可见性诊断', template)
        self.assertIn('诊断结论', template)
        self.assertIn('建议动作', template)


if __name__ == '__main__':
    unittest.main()
