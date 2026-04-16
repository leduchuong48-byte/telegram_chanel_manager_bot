from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import telegram_controllers as controllers_router


class TelegramControllersApiTest(unittest.TestCase):
    def test_create_list_make_primary_and_delete_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(
                json.dumps(
                    {
                        "database": {"path": str(db_path)},
                        "bot": {"admin_id": ""},
                    }
                ),
                encoding="utf-8",
            )
            manager = ConfigManager(config_path)
            controllers_router.set_config_manager(manager)

            create_1 = asyncio.run(
                controllers_router.create_telegram_controller(
                    controllers_router.ControllerCreateRequest(
                        user_id=1001,
                        display_name="alice",
                        enabled=True,
                        is_primary=True,
                        role="owner",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(create_1.success)

            create_2 = asyncio.run(
                controllers_router.create_telegram_controller(
                    controllers_router.ControllerCreateRequest(
                        user_id=1002,
                        display_name="bob",
                        enabled=True,
                        is_primary=False,
                        role="operator",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(create_2.success)

            listed = asyncio.run(controllers_router.list_telegram_controllers(_="admin", enabled_only=False))
            self.assertEqual(listed.count, 2)
            self.assertEqual(listed.data[0].user_id, 1001)
            self.assertTrue(listed.data[0].is_primary)
            self.assertEqual(listed.data[0].role, "owner")

            switched = asyncio.run(controllers_router.make_primary_controller(user_id=1002, _="admin"))
            self.assertTrue(switched.success)
            listed2 = asyncio.run(controllers_router.list_telegram_controllers(_="admin", enabled_only=False))
            self.assertEqual(listed2.data[0].user_id, 1002)
            self.assertTrue(listed2.data[0].is_primary)

            deleted = asyncio.run(controllers_router.delete_telegram_controller(user_id=1001, _="admin"))
            self.assertTrue(deleted.success)
            listed3 = asyncio.run(controllers_router.list_telegram_controllers(_="admin", enabled_only=False))
            self.assertEqual(listed3.count, 1)
            self.assertEqual(listed3.data[0].user_id, 1002)


if __name__ == "__main__":
    unittest.main()
