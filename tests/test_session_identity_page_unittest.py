from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class SessionIdentityPageTest(unittest.TestCase):
    def test_session_template_exposes_identity_center_model(self) -> None:
        template = (ROOT / 'app/templates/login_telegram.html').read_text(encoding='utf-8')
        self.assertIn('执行身份中心', template)
        self.assertIn('身份边界', template)
        self.assertIn('Web 登录身份', template)
        self.assertIn('/api/session/status', template)


if __name__ == '__main__':
    unittest.main()
