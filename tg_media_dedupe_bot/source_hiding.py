from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger("tg_media_dedupe_bot.source_hiding")


@dataclass
class HideStats:
    scanned: int = 0
    converted: int = 0
    skipped: int = 0
    failed: int = 0


def has_visible_source(message: Any) -> bool:
    return getattr(message, "forward_origin", None) is not None


async def copy_and_delete_message(bot: Any, message: Any) -> Any | None:
    """Copy a message in-place, then delete the source only after copy succeeds."""
    chat = getattr(message, "chat", None)
    chat_id = int(getattr(chat, "id", 0) or 0)
    message_id = int(getattr(message, "message_id", 0) or 0)
    if not chat_id or not message_id or not has_visible_source(message):
        return None
    copied = await bot.copy_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message_id)
    await bot.delete_message(chat_id=chat_id, message_id=message_id)
    return copied


async def hide_history(
    *,
    client: Any,
    bot: Any,
    chat_id: int,
    limit: int = 0,
    progress_cb: Callable[[HideStats], Awaitable[None]] | None = None,
) -> HideStats:
    stats = HideStats()
    async for message in client.iter_messages(chat_id, limit=(limit or None)):
        stats.scanned += 1
        if not getattr(message, "fwd_from", None):
            stats.skipped += 1
            continue
        try:
            # Bot API accepts the historical message id even though Telethon
            # is used only for enumeration.
            copied = await bot.copy_message(chat_id=chat_id, from_chat_id=chat_id, message_id=int(message.id))
            if copied is None:
                raise RuntimeError("copy_message returned no message")
            await bot.delete_message(chat_id=chat_id, message_id=int(message.id))
            stats.converted += 1
        except Exception:  # noqa: BLE001
            stats.failed += 1
            log.exception("history_source_hide_failed chat=%s message=%s", chat_id, getattr(message, "id", None))
        if progress_cb is not None:
            await progress_cb(stats)
    return stats
