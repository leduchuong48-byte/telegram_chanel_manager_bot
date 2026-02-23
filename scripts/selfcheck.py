#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

from tg_media_dedupe_bot.config import load_config
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.models import MediaItem


def main() -> None:
    cfg = load_config()
    if not cfg.bot_token:
        raise SystemExit("TG_BOT_TOKEN 缺失")

    db_parent = Path(cfg.db_path).expanduser().parent
    if not db_parent.exists():
        db_parent.mkdir(parents=True, exist_ok=True)
    if not os.access(db_parent, os.W_OK):
        raise SystemExit(f"DB_PATH 目录不可写: {db_parent}")

    test_db_path = Path(os.environ.get("SELFTEST_DB_PATH", "/tmp/tg_media_dedupe_selfcheck.db"))
    if test_db_path.exists():
        test_db_path.unlink()

    db = Database(test_db_path)
    try:
        now = int(time.time())
        item1 = MediaItem(
            chat_id=999999,
            message_id=100,
            media_key="botapi:photo:selfcheck",
            media_type="photo",
            file_unique_id="selfcheck",
            file_id="selfcheck_1",
            message_date=now,
        )
        item2 = MediaItem(
            chat_id=999999,
            message_id=101,
            media_key="botapi:photo:selfcheck",
            media_type="photo",
            file_unique_id="selfcheck",
            file_id="selfcheck_2",
            message_date=now + 1,
        )

        decision1 = db.process_media(item1)
        decision2 = db.process_media(item2)

        if decision1.message_id_to_delete is not None:
            raise SystemExit("首条消息不应被删除")
        if decision2.message_id_to_delete != 101:
            raise SystemExit("重复检测失败")

        stats = db.get_chat_stats(999999)
        if stats.get("media_messages", 0) < 2 or stats.get("unique_media", 0) < 1:
            raise SystemExit("统计结果异常")
    finally:
        db.close()
        if test_db_path.exists():
            test_db_path.unlink()

    print("selfcheck ok")


if __name__ == "__main__":
    main()
