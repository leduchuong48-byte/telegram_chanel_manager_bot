from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path('/vol1/1000/services/docker/chanel_manager_bot')


class TelegramControllersPageTest(unittest.TestCase):
    def test_main_has_telegram_controllers_route(self) -> None:
        main_py = (ROOT / 'app/main.py').read_text(encoding='utf-8')
        self.assertIn('@app.get("/telegram_controllers"', main_py)
        self.assertIn('"telegram_controllers.html"', main_py)

    def test_base_nav_has_telegram_controllers_entry(self) -> None:
        base_html = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('href="/telegram_controllers"', base_html)
        self.assertIn("Telegram 控制", base_html)

    def test_users_page_clarifies_web_users_only(self) -> None:
        users_html = (ROOT / 'app/templates/users.html').read_text(encoding='utf-8')
        self.assertIn('Web Admin 管理员账号', users_html)
        self.assertIn('不等同于 Telegram 控制用户', users_html)

    def test_bot_settings_admin_id_is_notify_only(self) -> None:
        bot_html = (ROOT / 'app/templates/bot_settings.html').read_text(encoding='utf-8')
        self.assertIn('启动通知 User ID', bot_html)

    def test_telegram_controllers_page_has_role_permissions_card(self) -> None:
        page = (ROOT / 'app/templates/telegram_controllers.html').read_text(encoding='utf-8')
        self.assertIn('角色权限说明', page)
        self.assertIn('owner', page)
        self.assertIn('admin', page)
        self.assertIn('operator', page)
        self.assertIn('readonly', page)

    def test_telegram_controllers_add_flow_has_success_error_feedback(self) -> None:
        page = (ROOT / 'app/templates/telegram_controllers.html').read_text(encoding='utf-8')
        self.assertIn('showToast(', page)
        self.assertIn('resp.ok', page)
        self.assertIn('添加控制用户失败', page)
        self.assertIn('已添加控制用户', page)

if __name__ == '__main__':
    unittest.main()
