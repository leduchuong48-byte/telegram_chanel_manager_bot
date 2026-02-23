#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from typing import Any

sys.path.insert(0, "/app")


def _report_failure(exc: BaseException) -> None:
    tb = traceback.extract_tb(exc.__traceback__)
    last = tb[-1] if tb else None
    if last is not None:
        print(f"TRACE {last.filename}:{last.lineno} in {last.name}")
    print(f"ERROR {exc}")


def _strictly_sorted(values: list[int], *, reverse: bool) -> bool:
    if len(values) <= 1:
        return True
    expected = sorted(values, reverse=reverse)
    return values == expected and len(set(values)) == len(values)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def _to_int(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"非法整数: {value}") from exc


def _pick_chat_target() -> tuple[str | None, int | None]:
    raw_chat = os.getenv("TEST_CHAT", "").strip()
    raw_chat_id = os.getenv("TEST_CHAT_ID", "").strip()
    if raw_chat:
        return raw_chat, None
    if raw_chat_id:
        return None, _to_int(raw_chat_id)
    raise RuntimeError("缺少 TEST_CHAT 或 TEST_CHAT_ID")


def _iter_ids(client: Any, entity: Any, *, limit: int, reverse: bool) -> list[int]:
    items: list[int] = []

    async def _inner() -> None:
        async for msg in client.iter_messages(entity, limit=limit, reverse=reverse):
            msg_id = getattr(msg, "id", None)
            if msg_id is None:
                continue
            items.append(int(msg_id))

    asyncio.get_event_loop().run_until_complete(_inner())
    return items


def main() -> int:
    from tg_media_dedupe_bot.config import load_config
    from tg_media_dedupe_bot.telethon_tags import _resolve_entity as _resolve_entity_telethon

    cfg = load_config()
    if cfg.tg_api_id is None or not cfg.tg_api_hash:
        raise RuntimeError("缺少 TG_API_ID/TG_API_HASH")

    test_chat, test_chat_id = _pick_chat_target()
    sample_raw = os.getenv("TEST_SAMPLE", "5").strip()
    sample = _to_int(sample_raw) if sample_raw else 5
    if sample <= 0:
        sample = 5

    try:
        from telethon import TelegramClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

    async def _run() -> None:
        client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)
        await client.connect()
        try:
            authorized = await client.is_user_authorized()
            if not authorized:
                raise RuntimeError("未检测到 Telethon 用户账号 session，请先完成 /session_login")

            entity = await _resolve_entity_telethon(
                client,
                chat=test_chat,
                bot_chat_id=test_chat_id,
                bot_chat_username=None,
                allow_dialog_lookup=True,
            )

            asc_ids: list[int] = []
            async for msg in client.iter_messages(entity, limit=sample, reverse=True):
                msg_id = getattr(msg, "id", None)
                if msg_id is None:
                    continue
                asc_ids.append(int(msg_id))

            desc_ids: list[int] = []
            async for msg in client.iter_messages(entity, limit=sample, reverse=False):
                msg_id = getattr(msg, "id", None)
                if msg_id is None:
                    continue
                desc_ids.append(int(msg_id))

            if not asc_ids or not desc_ids:
                raise RuntimeError("目标 chat 无可用消息，无法验证")

            if not _strictly_sorted(asc_ids, reverse=False):
                raise RuntimeError(f"reverse=True 顺序异常: {asc_ids}")
            if not _strictly_sorted(desc_ids, reverse=True):
                raise RuntimeError(f"reverse=False 顺序异常: {desc_ids}")

            oldest_id = asc_ids[0]
            newest_id = desc_ids[0]

            print("tag_rebuild_direction ok")
            print(f"oldest_id={oldest_id} newest_id={newest_id}")
            print(f"reverse=True first={oldest_id} sample={asc_ids}")
            print(f"reverse=False first={newest_id} sample={desc_ids}")
        finally:
            await client.disconnect()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        _report_failure(exc)
        raise
