from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tg_media_dedupe_bot.config import load_config
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.models import MediaItem


@dataclass(frozen=True)
class ScanResult:
    scanned: int
    decided_delete: int
    deleted: int
    failed: int


def build_scan_progress_snapshot(result: ScanResult, *, status: str) -> dict[str, int | str]:
    return {
        "status": status,
        "scanned": int(result.scanned),
        "matched": int(result.decided_delete),
        "acted": int(result.deleted),
        "failed": int(result.failed),
    }


def _raise_if_stop_requested(stop_checker: Callable[[], bool] | None) -> None:
    if stop_checker is not None and stop_checker():
        raise asyncio.CancelledError()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


_AD_LINK_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.IGNORECASE)
_MEDIA_BLACKLIST_TYPES = ("video", "audio", "photo", "text", "document")


def _contains_ad_text(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    if _AD_LINK_RE.search(text):
        return True
    folded = text.casefold()
    for keyword in keywords:
        if keyword and keyword.casefold() in folded:
            return True
    return False


def _parse_media_blacklist(raw: str | None) -> set[str]:
    if not raw:
        return set()
    items = [part.strip().lower() for part in raw.split(",")]
    return {item for item in items if item in _MEDIA_BLACKLIST_TYPES}


def _text_block_dir() -> Path:
    root = Path("data") / "text_block"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _text_block_global_path() -> Path:
    return _text_block_dir() / "global.txt"


def _text_block_chat_path(chat_id: int) -> Path:
    return _text_block_dir() / f"{chat_id}.txt"


def _parse_text_block_file(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//") or line.startswith(";"):
            continue
        normalized = line.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(line)
    return keywords


def _merge_text_block_keywords(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in group:
            cleaned = keyword.strip()
            if not cleaned:
                continue
            normalized = cleaned.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(cleaned)
    return merged


def _is_telethon_forwarded(message: Any) -> bool:
    return bool(getattr(message, "fwd_from", None))


def _telethon_media_type(message: Any) -> str | None:
    if getattr(message, "photo", None) is not None:
        return "photo"
    document = getattr(message, "document", None)
    if document is None:
        return None
    mime_type = getattr(document, "mime_type", "") or ""
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


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


async def run_scan(
    *,
    chat: str | None,
    bot_chat_id: int | None = None,
    bot_chat_username: str | None = None,
    limit: int,
    delete: bool,
    reverse: bool,
    as_bot: bool,
    interactive: bool = True,
    progress_cb: Callable[[ScanResult], Awaitable[None]] | None = None,
    progress_interval: int = 500,
    stop_checker: Callable[[], bool] | None = None,
) -> ScanResult:
    config = load_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("tg_media_dedupe_bot.scan")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.network").setLevel(logging.WARNING)

    if config.tg_api_id is None or not config.tg_api_hash:
        raise RuntimeError("历史扫描需要 TG_API_ID/TG_API_HASH（见 .env.example）")

    try:
        from telethon import TelegramClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

    if as_bot:
        raise RuntimeError(
            "MTProto 的 bot 身份无法拉取历史消息（GetHistoryRequest 被限制），因此无法回溯扫描。"
            "请改用 Telethon 用户账号 session（先完成登录授权），或仅使用 Bot API 实时处理新消息。"
        )

    db = Database(config.db_path)
    result = ScanResult(scanned=0, decided_delete=0, deleted=0, failed=0)
    db_keywords = db.list_text_block_keywords()
    global_keywords = _parse_text_block_file(_text_block_global_path())
    chat_keywords: list[str] = []
    if bot_chat_id is not None:
        chat_keywords = _parse_text_block_file(_text_block_chat_path(bot_chat_id))
    block_keywords = _merge_text_block_keywords(db_keywords, global_keywords, chat_keywords)
    raw_blacklist = None
    if bot_chat_id is not None:
        raw_blacklist = db.get_setting(f"chat:{bot_chat_id}:media_blacklist")
    blacklist = _parse_media_blacklist(raw_blacklist)

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

        async def handle_delete(chat_id: int, message_id: int, media_key: str, reason: str) -> None:
            nonlocal result
            result = ScanResult(
                scanned=result.scanned,
                decided_delete=result.decided_delete + 1,
                deleted=result.deleted,
                failed=result.failed,
            )
            db.add_pending_deletion(
                chat_id=chat_id,
                message_id=message_id,
                media_key=media_key,
                reason=reason,
            )

            if not delete:
                log.warning(
                    "dry_run scan_skip_delete chat=%s delete_msg=%s reason=%s",
                    chat_id,
                    message_id,
                    reason,
                )
                return

            existing = db.get_deletion_record(chat_id, message_id)
            if existing is not None and (existing.result == "success" or not config.retry_failed_deletes):
                if existing.result == "success":
                    db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
                return

            try:
                await client.delete_messages(entity, [message_id])
            except Exception as exc:  # noqa: BLE001
                db.record_deletion_attempt(
                    chat_id=chat_id,
                    message_id=message_id,
                    media_key=media_key,
                    result="failed",
                    error=str(exc),
                )
                result = ScanResult(
                    scanned=result.scanned,
                    decided_delete=result.decided_delete,
                    deleted=result.deleted,
                    failed=result.failed + 1,
                )
                log.exception("delete_failed chat=%s msg=%s reason=%s", chat_id, message_id, reason)
                return

            db.record_deletion_attempt(
                chat_id=chat_id,
                message_id=message_id,
                media_key=media_key,
                result="success",
                error=None,
            )
            db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
            result = ScanResult(
                scanned=result.scanned,
                decided_delete=result.decided_delete,
                deleted=result.deleted + 1,
                failed=result.failed,
            )

        async for msg in client.iter_messages(entity, limit=limit or None, reverse=reverse):
            _raise_if_stop_requested(stop_checker)
            if msg is None or msg.id is None or msg.chat_id is None:
                continue

            result = ScanResult(
                scanned=result.scanned + 1,
                decided_delete=result.decided_delete,
                deleted=result.deleted,
                failed=result.failed,
            )

            text = str(getattr(msg, "message", "") or "")
            media_type = _telethon_media_type(msg)
            if _is_telethon_forwarded(msg) and _contains_ad_text(text, block_keywords):
                await handle_delete(
                    int(msg.chat_id),
                    int(msg.id),
                    f"mtproto:ad:{msg.id}",
                    "ad_block",
                )
                if progress_cb and progress_interval > 0 and result.scanned % progress_interval == 0:
                    await progress_cb(result)
                _raise_if_stop_requested(stop_checker)
                continue

            if media_type is None:
                if "text" in blacklist and text and getattr(msg, "action", None) is None:
                    await handle_delete(
                        int(msg.chat_id),
                        int(msg.id),
                        f"mtproto:text:{msg.id}",
                        "media_blacklist:text",
                    )
                if progress_cb and progress_interval > 0 and result.scanned % progress_interval == 0:
                    await progress_cb(result)
                _raise_if_stop_requested(stop_checker)
                continue

            if media_type in blacklist:
                await handle_delete(
                    int(msg.chat_id),
                    int(msg.id),
                    f"mtproto:{media_type}:{msg.id}",
                    f"media_blacklist:{media_type}",
                )
                if progress_cb and progress_interval > 0 and result.scanned % progress_interval == 0:
                    await progress_cb(result)
                _raise_if_stop_requested(stop_checker)
                continue

            media_key: str | None = None
            file_id: str | None = None
            file_unique_id: str | None = None

            if media_type == "photo" and getattr(msg, "photo", None) is not None:
                media_key = f"mtproto:photo:{msg.photo.id}"
            elif getattr(msg, "document", None) is not None:
                media_key = f"mtproto:document:{msg.document.id}"

            if not media_key:
                continue

            item = MediaItem(
                chat_id=int(msg.chat_id),
                message_id=int(msg.id),
                media_key=media_key,
                media_type=media_type,
                file_unique_id=file_unique_id,
                file_id=file_id,
                message_date=int(msg.date.timestamp()) if getattr(msg, "date", None) else 0,
            )

            decision = db.process_media(item)
            result = ScanResult(
                scanned=result.scanned,
                decided_delete=result.decided_delete + (1 if decision.message_id_to_delete else 0),
                deleted=result.deleted,
                failed=result.failed,
            )

            if progress_cb and progress_interval > 0 and result.scanned % progress_interval == 0:
                await progress_cb(result)
                _raise_if_stop_requested(stop_checker)

            if decision.message_id_to_delete is None:
                continue

            delete_id = decision.message_id_to_delete
            db.add_pending_deletion(
                chat_id=item.chat_id,
                message_id=delete_id,
                media_key=item.media_key,
                reason=decision.reason,
            )

            if not delete:
                log.warning(
                    "dry_run scan_skip_delete chat=%s delete_msg=%s canonical=%s key=%s reason=%s",
                    item.chat_id,
                    delete_id,
                    decision.canonical_message_id,
                    item.media_key,
                    decision.reason,
                )
                continue

            existing = db.get_deletion_record(item.chat_id, delete_id)
            if existing is not None and (existing.result == "success" or not config.retry_failed_deletes):
                if existing.result == "success":
                    db.remove_pending_deletion(chat_id=item.chat_id, message_id=delete_id)
                continue

            try:
                await client.delete_messages(entity, [delete_id])
            except Exception as exc:  # noqa: BLE001
                db.record_deletion_attempt(
                    chat_id=item.chat_id,
                    message_id=delete_id,
                    media_key=item.media_key,
                    result="failed",
                    error=str(exc),
                )
                result = ScanResult(
                    scanned=result.scanned,
                    decided_delete=result.decided_delete,
                    deleted=result.deleted,
                    failed=result.failed + 1,
                )
                log.exception("delete_failed chat=%s msg=%s", item.chat_id, delete_id)
                continue

            db.record_deletion_attempt(
                chat_id=item.chat_id,
                message_id=delete_id,
                media_key=item.media_key,
                result="success",
                error=None,
            )
            db.remove_pending_deletion(chat_id=item.chat_id, message_id=delete_id)
            result = ScanResult(
                scanned=result.scanned,
                decided_delete=result.decided_delete,
                deleted=result.deleted + 1,
                failed=result.failed,
            )

            if result.scanned % 200 == 0:
                log.info(
                    "progress scanned=%s decided_delete=%s deleted=%s failed=%s",
                    result.scanned,
                    result.decided_delete,
                    result.deleted,
                    result.failed,
                )
    finally:
        await _maybe_await(client.disconnect())
        db.close()

    log.info(
        "done scanned=%s decided_delete=%s deleted=%s failed=%s",
        result.scanned,
        result.decided_delete,
        result.deleted,
        result.failed,
    )
    if progress_cb:
        await progress_cb(result)
    return result
