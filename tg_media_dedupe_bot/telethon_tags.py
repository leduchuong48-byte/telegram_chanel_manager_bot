from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any

from tg_media_dedupe_bot.config import load_config


@dataclass(frozen=True)
class TagScanProgress:
    scanned: int
    unique_tags: int
    total_tags: int


@dataclass(frozen=True)
class TagScanResult:
    scanned: int
    tag_counts: dict[str, int]
    total_tags: int


def build_tag_progress_snapshot(progress: TagScanProgress | TagScanResult, *, status: str) -> dict[str, int | str]:
    if isinstance(progress, TagScanResult):
        unique_tags = len(progress.tag_counts)
        total_tags = progress.total_tags
        scanned = progress.scanned
    else:
        unique_tags = progress.unique_tags
        total_tags = progress.total_tags
        scanned = progress.scanned
    return {
        "status": status,
        "scanned": int(scanned),
        "unique_tags": int(unique_tags),
        "total_tags": int(total_tags),
    }


def _raise_if_stop_requested(stop_checker: Callable[[], bool] | None) -> None:
    if stop_checker is not None and stop_checker():
        raise asyncio.CancelledError()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_username(username: str) -> str:
    name = username.strip()
    if not name:
        return ""
    return name if name.startswith("@") else f"@{name}"


def _is_botapi_channel_chat_id(chat_id: int) -> bool:
    return str(chat_id).startswith("-100")


def _botapi_channel_id(chat_id: int) -> int:
    return int(str(chat_id)[4:])


async def _resolve_entity(
    client: Any,
    *,
    chat: str | None,
    bot_chat_id: int | None,
    bot_chat_username: str | None,
    allow_dialog_lookup: bool,
) -> Any:
    if chat:
        normalized = chat.strip()
        if normalized.lstrip("-").isdigit():
            return await _resolve_entity(
                client,
                chat=None,
                bot_chat_id=int(normalized),
                bot_chat_username=None,
                allow_dialog_lookup=allow_dialog_lookup,
            )
        return await client.get_entity(chat)

    if bot_chat_username:
        return await client.get_entity(_normalize_username(bot_chat_username))

    if bot_chat_id is None:
        raise RuntimeError("缺少 chat 参数")

    if bot_chat_id >= 0:
        raise RuntimeError("仅支持群组/频道 chat_id（应为负数）")

    if _is_botapi_channel_chat_id(bot_chat_id):
        target_id = _botapi_channel_id(bot_chat_id)
        try:
            from telethon.tl.types import PeerChannel  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Telethon 依赖异常") from exc

        try:
            return await client.get_entity(PeerChannel(target_id))
        except Exception as exc:  # noqa: BLE001
            if allow_dialog_lookup:
                async for dialog in client.iter_dialogs():
                    entity = getattr(dialog, "entity", None)
                    if entity is None:
                        continue
                    if getattr(entity, "id", None) == target_id and entity.__class__.__name__ == "Channel":
                        return entity
            raise RuntimeError("无法解析该频道/超级群，请使用 @username 或邀请链接作为 chat 参数") from exc

    try:
        from telethon.tl.types import PeerChat  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Telethon 依赖异常") from exc

    try:
        return await client.get_entity(PeerChat(-bot_chat_id))
    except Exception as exc:  # noqa: BLE001
        if allow_dialog_lookup:
            target_id = -bot_chat_id
            async for dialog in client.iter_dialogs():
                entity = getattr(dialog, "entity", None)
                if entity is None:
                    continue
                if getattr(entity, "id", None) == target_id and entity.__class__.__name__ == "Chat":
                    return entity
        raise RuntimeError("无法解析该群组，请使用 @username 或邀请链接作为 chat 参数") from exc


_HASHTAG_RE = re.compile(r"(?<!\w)#(\w{1,64})")
_INVALID_TAG_RE = re.compile(r"^(?:\d+|(?=.*\d)(?=.*[A-Za-z])[0-9A-Za-z]+)$")


def _is_valid_tag(tag: str) -> bool:
    if not tag:
        return False
    return _INVALID_TAG_RE.match(tag) is None


def _normalize_tag_text(tag: str) -> str:
    return tag.strip().lstrip("#").casefold()


def _dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        normalized = _normalize_tag_text(tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _extract_hashtags(text: str, entities: list[Any] | None) -> list[str]:
    if not text:
        return []

    if entities:
        tags: list[str] = []
        for ent in entities:
            if ent.__class__.__name__ != "MessageEntityHashtag":
                continue
            offset = int(getattr(ent, "offset", 0))
            length = int(getattr(ent, "length", 0))
            if length <= 1:
                continue
            raw = text[offset : offset + length]
            if not raw.startswith("#") or len(raw) <= 1:
                continue
            tag = raw[1:]
            if tag and _is_valid_tag(tag):
                tags.append(tag)
        if tags:
            return _dedupe_tags(tags)

    tags = [m.group(1) for m in _HASHTAG_RE.finditer(text) if _is_valid_tag(m.group(1))]
    return _dedupe_tags(tags)


async def run_tag_scan(
    *,
    chat: str | None,
    bot_chat_id: int | None = None,
    bot_chat_username: str | None = None,
    limit: int,
    reverse: bool,
    interactive: bool = True,
    progress_cb: Callable[[TagScanProgress], Awaitable[None]] | None = None,
    progress_interval: int = 500,
    stop_checker: Callable[[], bool] | None = None,
) -> TagScanResult:
    config = load_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("tg_media_dedupe_bot.tags")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.network").setLevel(logging.WARNING)

    if config.tg_api_id is None or not config.tg_api_hash:
        raise RuntimeError("标签汇总需要 TG_API_ID/TG_API_HASH（见 .env.example）")

    try:
        from telethon import TelegramClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

    counts: Counter[str] = Counter()
    scanned = 0
    total_tags = 0

    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
    await _maybe_await(client.connect())
    try:
        if interactive:
            await _maybe_await(client.start())
        else:
            authorized = await _maybe_await(client.is_user_authorized())
            if not authorized:
                raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")

        me = await _maybe_await(client.get_me())
        if getattr(me, "bot", False):
            raise RuntimeError(
                "当前 TG_SESSION 授权的是 bot 账号（受 MTProto 限制无法回溯历史）。"
                "请删除该 session 文件并用用户账号重新授权。"
            )

        entity = await _resolve_entity(
            client,
            chat=chat,
            bot_chat_id=bot_chat_id,
            bot_chat_username=bot_chat_username,
            allow_dialog_lookup=True,
        )

        async for msg in client.iter_messages(entity, limit=limit or None, reverse=reverse):
            _raise_if_stop_requested(stop_checker)
            if msg is None or msg.id is None:
                continue
            scanned += 1

            text = str(getattr(msg, "message", "") or "")
            entities = getattr(msg, "entities", None)
            tags = _extract_hashtags(text, entities)
            if tags:
                for tag in tags:
                    counts[tag] += 1
                    total_tags += 1

            if progress_cb and progress_interval > 0 and scanned % progress_interval == 0:
                await progress_cb(TagScanProgress(scanned=scanned, unique_tags=len(counts), total_tags=total_tags))
                _raise_if_stop_requested(stop_checker)
    finally:
        await _maybe_await(client.disconnect())

    if progress_cb:
        await progress_cb(TagScanProgress(scanned=scanned, unique_tags=len(counts), total_tags=total_tags))

    log.info("done scanned=%s unique_tags=%s total_tags=%s", scanned, len(counts), total_tags)
    return TagScanResult(scanned=scanned, tag_counts=dict(counts), total_tags=total_tags)
