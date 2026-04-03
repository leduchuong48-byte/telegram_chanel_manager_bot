"""Cleaner API routes for message purge."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import RPCError

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.telethon_runtime import (
    discover_dialog_targets,
    get_bot_config,
    get_target_chat_tokens,
    map_telethon_exception,
    open_web_client,
    parse_target_chat_token,
)
from tg_media_dedupe_bot.telethon_scan import _resolve_entity as _resolve_entity_telethon

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cleaner", tags=["cleaner"])

_config_manager: ConfigManager | None = None


class BatchDeleteRequest(BaseModel):
    count: int = Field(..., ge=1, le=5000)
    target: str | None = None


class DeleteByTypeRequest(BaseModel):
    types: list[str] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=5000)
    target: str | None = None


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


def _get_target_tokens(bot_config: dict[str, Any]) -> list[str]:
    return get_target_chat_tokens(bot_config)


async def _resolve_target_entities(client: TelegramClient, tokens: list[str]) -> list[tuple[str, Any]]:
    entities: list[tuple[str, Any]] = []
    for token in tokens:
        chat, bot_chat_id = parse_target_chat_token(token)
        try:
            entity = await _resolve_entity_telethon(
                client,
                chat=chat,
                bot_chat_id=bot_chat_id,
                bot_chat_username=None,
                allow_dialog_lookup=True,
            )
        except Exception as exc:
            logger.warning("resolve_target_failed target=%s error=%s", token, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法解析目标群组/频道：{token}",
            ) from exc
        entities.append((token, entity))
    return entities


def _resolve_target_from_payload(payload: Any) -> str | None:
    if payload is None:
        return None
    raw = getattr(payload, "target", None)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


async def _resolve_targets(
    client: TelegramClient,
    bot_config: dict[str, Any],
    *,
    requested_target: str | None = None,
) -> list[tuple[str, Any]]:
    if requested_target:
        return await _resolve_target_entities(client, [requested_target])

    tokens = _get_target_tokens(bot_config)
    if tokens:
        return await _resolve_target_entities(client, tokens)

    targets = await discover_dialog_targets(client)
    if targets:
        return targets

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "未配置 target_chat_ids，且自动发现不到可管理群组/频道；"
            "请确认 Web 登录账号已加入目标群并具备管理员权限"
        ),
    )


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
    bot_config = get_bot_config(config_manager.get_config())
    try:
        async with open_web_client(
            bot_config,
            unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
            bot_session_detail="当前会话为 Bot 账号，无法执行清理",
            connect_error_detail="连接 Telethon 会话失败",
        ) as client:
            requested_target = _resolve_target_from_payload(payload)
            targets = await _resolve_targets(client, bot_config, requested_target=requested_target)
            results: list[dict[str, Any]] = []
            total_deleted = 0
            total_scanned = 0
            for token, entity in targets:
                message_ids: list[int] = []
                async for msg in client.iter_messages(entity, limit=payload.count):
                    msg_id = getattr(msg, "id", None)
                    if isinstance(msg_id, int) and msg_id > 0:
                        message_ids.append(msg_id)
                deleted = await _delete_messages(client, entity, message_ids)
                total_deleted += deleted
                total_scanned += len(message_ids)
                results.append(
                    {
                        "target": token,
                        "requested": payload.count,
                        "scanned": len(message_ids),
                        "deleted": deleted,
                    }
                )
            return {
                "success": True,
                "targets": len(results),
                "scanned": total_scanned,
                "deleted": total_deleted,
                "total_scanned": total_scanned,
                "total_deleted": total_deleted,
                "results": results,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise map_telethon_exception(exc, default_detail="执行批量删除失败") from exc


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
    bot_config = get_bot_config(config_manager.get_config())
    try:
        async with open_web_client(
            bot_config,
            unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
            bot_session_detail="当前会话为 Bot 账号，无法执行清理",
            connect_error_detail="连接 Telethon 会话失败",
        ) as client:
            requested_target = _resolve_target_from_payload(payload)
            targets = await _resolve_targets(client, bot_config, requested_target=requested_target)
            results: list[dict[str, Any]] = []
            total_deleted = 0
            total_scanned = 0
            for token, entity in targets:
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
                total_deleted += deleted
                total_scanned += scanned
                results.append(
                    {
                        "target": token,
                        "scanned": scanned,
                        "matched": len(message_ids),
                        "deleted": deleted,
                    }
                )
            return {
                "success": True,
                "targets": len(results),
                "scanned": total_scanned,
                "deleted": total_deleted,
                "total_scanned": total_scanned,
                "total_deleted": total_deleted,
                "results": results,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise map_telethon_exception(exc, default_detail="按类型删除失败") from exc
