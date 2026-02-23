"""Cleaner API routes for message purge."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import RPCError

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from tg_media_dedupe_bot.telethon_scan import _resolve_entity as _resolve_entity_telethon

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cleaner", tags=["cleaner"])

_config_manager: ConfigManager | None = None


class BatchDeleteRequest(BaseModel):
    count: int = Field(..., ge=1, le=5000)


class DeleteByTypeRequest(BaseModel):
    types: list[str] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=5000)


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def _get_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _get_bot_config(config: dict[str, Any]) -> dict[str, Any]:
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}
    return bot_config


def _parse_target_chat(bot_config: dict[str, Any]) -> tuple[str | None, int | None]:
    raw = str(bot_config.get("target_chat_id") or "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未配置 target_chat_id，请先在 Bot 设置中填写目标群组/频道",
        )
    if raw.lstrip("-").isdigit():
        return None, int(raw)
    return raw, None


def _get_telethon_credentials(bot_config: dict[str, Any]) -> tuple[int, str, str]:
    api_id_raw = bot_config.get("api_id") or os.getenv("TG_API_ID", "")
    api_hash = str(bot_config.get("api_hash") or os.getenv("TG_API_HASH", "")).strip()
    session = str(bot_config.get("tg_session") or os.getenv("TG_SESSION", "") or "./sessions/user").strip()

    api_id: int | None = None
    if isinstance(api_id_raw, int):
        api_id = api_id_raw
    elif isinstance(api_id_raw, str) and api_id_raw.strip():
        try:
            api_id = int(api_id_raw.strip())
        except ValueError:
            api_id = None

    if api_id is None or not api_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 api_id 或 api_hash，请先在 Bot 设置中完善",
        )
    return api_id, api_hash, session


async def _build_client(bot_config: dict[str, Any]) -> TelegramClient:
    api_id, api_hash, session = _get_telethon_credentials(bot_config)
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未检测到 Telethon 用户账号 session，请先在账号管理中登录",
        )
    me = await client.get_me()
    if getattr(me, "bot", False):
        await client.disconnect()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前会话为 Bot 账号，无法执行清理",
        )
    return client


async def _resolve_target_entity(client: TelegramClient, bot_config: dict[str, Any]) -> Any:
    chat, bot_chat_id = _parse_target_chat(bot_config)
    try:
        return await _resolve_entity_telethon(
            client,
            chat=chat,
            bot_chat_id=bot_chat_id,
            bot_chat_username=None,
            allow_dialog_lookup=True,
        )
    except Exception as exc:
        logger.warning("resolve_target_failed error=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无法解析目标群组/频道，请检查 target_chat_id",
        ) from exc


def _chunk_list(items: list[int], size: int = 100) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _delete_messages(client: TelegramClient, entity: Any, message_ids: list[int]) -> int:
    deleted = 0
    for chunk in _chunk_list(message_ids, 100):
        if not chunk:
            continue
        try:
            await client.delete_messages(entity, chunk)
            deleted += len(chunk)
        except RPCError as exc:
            logger.warning("delete_messages_failed error=%s ids=%s", exc, chunk)
        except Exception as exc:
            logger.warning("delete_messages_failed error=%s ids=%s", exc, chunk)
    return deleted


def _message_matches(msg: Any, types: set[str]) -> bool:
    if "photo" in types and getattr(msg, "photo", None) is not None:
        return True
    if "video" in types and getattr(msg, "video", None) is not None:
        return True
    if "sticker" in types and getattr(msg, "sticker", None) is not None:
        return True
    if "text" in types:
        if getattr(msg, "media", None) is None and getattr(msg, "message", None):
            return True
    return False


@router.post("/batch_delete")
async def batch_delete(
    payload: BatchDeleteRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    """Delete the latest N messages."""
    config_manager = _get_config_manager()
    bot_config = _get_bot_config(config_manager.get_config())
    client = await _build_client(bot_config)
    try:
        entity = await _resolve_target_entity(client, bot_config)
        message_ids: list[int] = []
        async for msg in client.iter_messages(entity, limit=payload.count):
            msg_id = getattr(msg, "id", None)
            if isinstance(msg_id, int) and msg_id > 0:
                message_ids.append(msg_id)
        deleted = await _delete_messages(client, entity, message_ids)
        return {"success": True, "deleted": deleted, "requested": payload.count}
    finally:
        await client.disconnect()


@router.post("/delete_by_type")
async def delete_by_type(
    payload: DeleteByTypeRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    """Delete messages by type."""
    types = {t.strip().lower() for t in payload.types if isinstance(t, str)}
    types = {t for t in types if t in {"text", "photo", "video", "sticker"}}
    if not types:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择需要清理的类型")

    config_manager = _get_config_manager()
    bot_config = _get_bot_config(config_manager.get_config())
    client = await _build_client(bot_config)
    try:
        entity = await _resolve_target_entity(client, bot_config)
        message_ids: list[int] = []
        scanned = 0
        async for msg in client.iter_messages(entity, limit=payload.limit):
            scanned += 1
            msg_id = getattr(msg, "id", None)
            if not isinstance(msg_id, int) or msg_id <= 0:
                continue
            if _message_matches(msg, types):
                message_ids.append(msg_id)
        deleted = await _delete_messages(client, entity, message_ids)
        return {"success": True, "deleted": deleted, "scanned": scanned}
    finally:
        await client.disconnect()
