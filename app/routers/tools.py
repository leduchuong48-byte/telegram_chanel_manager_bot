"""Maintenance tools API routes."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from telethon import TelegramClient
from telethon.errors import RPCError

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from tg_media_dedupe_bot.telethon_scan import _resolve_entity as _resolve_entity_telethon

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_config_manager: ConfigManager | None = None


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
        return {}
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
            detail="当前会话为 Bot 账号，无法执行维护指令",
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


def _get_tag_count(bot_config: dict[str, Any]) -> int:
    raw = bot_config.get("tag_count", 0)
    tag_count: int | None = None
    if isinstance(raw, int):
        tag_count = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            tag_count = int(raw.strip())
        except ValueError:
            tag_count = None
    if tag_count is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tag_count 配置无效")
    if tag_count < 1 or tag_count > 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tag_count 需在 1-10 之间")
    return tag_count


async def _send_command(command: str) -> None:
    config_manager = _get_config_manager()
    bot_config = _get_bot_config(config_manager.get_config())
    client = await _build_client(bot_config)
    try:
        entity = await _resolve_target_entity(client, bot_config)
        await client.send_message(entity, command)
    except RPCError as exc:
        logger.warning("send_command_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发送命令失败") from exc
    finally:
        await client.disconnect()


@router.post("/fix_tag_count", response_model=MessageResponse)
async def fix_tag_count(
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Trigger tag count update (maps to /tag_count)."""
    config_manager = _get_config_manager()
    bot_config = _get_bot_config(config_manager.get_config())
    tag_count = _get_tag_count(bot_config)
    await _send_command(f"/tag_count {tag_count}")
    return MessageResponse(success=True, message=f"已发送补标签数量设置：{tag_count}")


@router.post("/reload_tags", response_model=MessageResponse)
async def reload_tags(
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Trigger tag reload/update (maps to /tag_update)."""
    await _send_command("/tag_update")
    return MessageResponse(success=True, message="已发送标签更新指令")


@router.post("/reload_system", response_model=MessageResponse)
async def reload_system(
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Stop current tasks (maps to /tag_stop)."""
    await _send_command("/tag_stop")
    return MessageResponse(success=True, message="已发送系统重置指令")
