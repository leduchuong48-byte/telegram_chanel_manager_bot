"""Maintenance tools API routes."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from telegram import Bot
from telegram.error import TelegramError
from telethon import TelegramClient
from telethon.errors import RPCError

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.core.telethon_runtime import (
    discover_dialog_targets,
    entity_to_target_token,
    get_bot_config,
    get_target_chat_tokens,
    map_telethon_exception,
    open_web_client,
    parse_target_chat_token,
)
from tg_media_dedupe_bot.telethon_scan import _resolve_entity as _resolve_entity_telethon

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_config_manager: ConfigManager | None = None
_BOT_MANAGE_STATUSES = {"administrator", "creator"}
_BOT_API_TIMEOUT_SECONDS = 20
_REGISTRY_LIMIT = 500
_VERIFIED_BY_GET_CHAT_MEMBER = "get_chat_member"


class ToolExecuteRequest(BaseModel):
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


def _resolve_database_path(config: dict[str, Any]) -> Path:
    database = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(database, dict):
        database = {}
    raw = str(database.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _read_registry_rows(
    config: dict[str, Any],
    *,
    manageable_only: bool,
    limit: int = _REGISTRY_LIMIT,
) -> list[dict[str, Any]]:
    db_path = _resolve_database_path(config)
    if not db_path.exists():
        return []

    safe_limit = max(1, min(int(limit), 5000))
    clauses = ["is_active = 1"]
    if manageable_only:
        clauses.append("bot_can_manage = 1")
    where_sql = " AND ".join(clauses)

    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='managed_chats'"
        ).fetchone()
        if table_exists is None:
            return []
        _ensure_registry_table(conn)
        rows = conn.execute(
            f"""
            SELECT
              chat_id, title, username, chat_type, source,
              is_active, bot_status, bot_can_manage,
              verified_at, verified_by, updated_at
            FROM managed_chats
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        chat_id = int(row["chat_id"])
        username = str(row["username"] or "").strip().lstrip("@")
        title = str(row["title"] or "").strip()
        if not title:
            title = f"@{username}" if username else str(chat_id)
        items.append(
            {
                "token": str(chat_id),
                "title": title,
                "username": username,
                "chat_type": str(row["chat_type"] or "").strip().lower(),
                "source": str(row["source"] or "registry").strip() or "registry",
                "bot_status": str(row["bot_status"] or "unknown").strip().lower() or "unknown",
                "bot_can_manage": int(row["bot_can_manage"]) == 1,
                "verified_at": int(row["verified_at"] or 0),
                "verified_by": str(row["verified_by"] or "").strip(),
            }
        )
    return items


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns: set[str] = set()
    for row in rows:
        if isinstance(row, sqlite3.Row):
            columns.add(str(row["name"]))
        else:
            columns.add(str(row[1]))
    return columns


def _ensure_registry_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_chats (
          chat_id INTEGER NOT NULL PRIMARY KEY,
          title TEXT NOT NULL DEFAULT '',
          username TEXT NOT NULL DEFAULT '',
          chat_type TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1,
          bot_status TEXT NOT NULL DEFAULT 'unknown',
          bot_can_manage INTEGER NOT NULL DEFAULT 0,
          last_seen_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          verified_at INTEGER NOT NULL DEFAULT 0,
          verified_by TEXT NOT NULL DEFAULT ''
        );
        """
    )
    columns = _table_columns(conn, "managed_chats")
    migrations: list[tuple[str, str]] = [
        ("source", "ALTER TABLE managed_chats ADD COLUMN source TEXT NOT NULL DEFAULT ''"),
        ("bot_status", "ALTER TABLE managed_chats ADD COLUMN bot_status TEXT NOT NULL DEFAULT 'unknown'"),
        ("bot_can_manage", "ALTER TABLE managed_chats ADD COLUMN bot_can_manage INTEGER NOT NULL DEFAULT 0"),
        ("last_seen_at", "ALTER TABLE managed_chats ADD COLUMN last_seen_at INTEGER NOT NULL DEFAULT 0"),
        ("updated_at", "ALTER TABLE managed_chats ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
        ("verified_at", "ALTER TABLE managed_chats ADD COLUMN verified_at INTEGER NOT NULL DEFAULT 0"),
        ("verified_by", "ALTER TABLE managed_chats ADD COLUMN verified_by TEXT NOT NULL DEFAULT ''"),
    ]
    for column, sql in migrations:
        if column not in columns:
            conn.execute(sql)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_managed_chats_active_updated
        ON managed_chats(is_active, updated_at DESC);
        """
    )


def _sync_registry_rows(config: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    db_path = _resolve_database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        _ensure_registry_table(conn)
        now = int(time.time())
        with conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO managed_chats(
                      chat_id, title, username, chat_type, source,
                      is_active, bot_status, bot_can_manage,
                      last_seen_at, updated_at, verified_at, verified_by
                    )
                    VALUES(?, ?, ?, ?, ?, 1, 'unchecked', 1, ?, ?, 0, '')
                    ON CONFLICT(chat_id) DO UPDATE SET
                      title=excluded.title,
                      username=excluded.username,
                      chat_type=excluded.chat_type,
                      source=excluded.source,
                      is_active=1,
                      last_seen_at=excluded.last_seen_at,
                      updated_at=excluded.updated_at
                    """,
                    (
                        int(row["chat_id"]),
                        str(row.get("title", "")).strip(),
                        str(row.get("username", "")).strip().lstrip("@"),
                        str(row.get("chat_type", "unknown")).strip().lower(),
                        str(row.get("source", "web_sync")).strip() or "web_sync",
                        now,
                        now,
                    ),
                )
    finally:
        conn.close()
    return len(rows)


def _update_registry_manage_status(config: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    db_path = _resolve_database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        _ensure_registry_table(conn)
        now = int(time.time())
        with conn:
            for row in rows:
                verified_at = row.get("verified_at")
                if isinstance(verified_at, int):
                    verified_ts = max(0, verified_at)
                else:
                    verified_ts = now
                conn.execute(
                    """
                    INSERT INTO managed_chats(
                      chat_id, title, username, chat_type, source,
                      is_active, bot_status, bot_can_manage,
                      last_seen_at, updated_at, verified_at, verified_by
                    )
                    VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                      title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE managed_chats.title END,
                      username=CASE WHEN excluded.username <> '' THEN excluded.username ELSE managed_chats.username END,
                      chat_type=CASE
                        WHEN excluded.chat_type <> '' AND excluded.chat_type <> 'unknown' THEN excluded.chat_type
                        ELSE managed_chats.chat_type
                      END,
                      source=CASE
                        WHEN managed_chats.source = '' THEN excluded.source
                        ELSE managed_chats.source
                      END,
                      is_active=1,
                      bot_status=excluded.bot_status,
                      bot_can_manage=excluded.bot_can_manage,
                      last_seen_at=excluded.last_seen_at,
                      updated_at=excluded.updated_at,
                      verified_at=excluded.verified_at,
                      verified_by=excluded.verified_by
                    """,
                    (
                        int(row["chat_id"]),
                        str(row.get("title", "")).strip(),
                        str(row.get("username", "")).strip().lstrip("@"),
                        str(row.get("chat_type", "unknown")).strip().lower(),
                        str(row.get("source", "runtime_verify")).strip() or "runtime_verify",
                        str(row.get("bot_status", "unknown")).strip().lower() or "unknown",
                        int(bool(row.get("bot_can_manage"))),
                        now,
                        now,
                        verified_ts,
                        str(row.get("verified_by", _VERIFIED_BY_GET_CHAT_MEMBER)).strip()
                        or _VERIFIED_BY_GET_CHAT_MEMBER,
                    ),
                )
    finally:
        conn.close()


def _registry_tokens(config: dict[str, Any], *, manageable_only: bool) -> list[str]:
    rows = _read_registry_rows(config, manageable_only=manageable_only)
    return [str(row["token"]) for row in rows if str(row.get("token", "")).strip()]


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


async def _resolve_target_entities_best_effort(
    client: TelegramClient,
    tokens: list[str],
) -> list[tuple[str, Any]]:
    entities: list[tuple[str, Any]] = []
    failed: list[str] = []
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
            failed.append(token)
            continue
        entities.append((token, entity))

    if entities:
        if failed:
            logger.warning(
                "resolve_target_partial_failed success=%s failed=%s",
                len(entities),
                ",".join(failed[:20]),
            )
        return entities

    if failed:
        failed_preview = ",".join(failed[:10])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"目标群组均不可访问（示例：{failed_preview}）。请确认 Web 登录账号已加入目标群组",
        )
    return []


def _resolve_target_from_payload(payload: ToolExecuteRequest | None) -> str | None:
    if payload is None:
        return None
    raw = (payload.target or "").strip()
    return raw or None


def _format_entity_title(entity: Any) -> str:
    title = getattr(entity, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    first_name = (getattr(entity, "first_name", "") or "").strip()
    last_name = (getattr(entity, "last_name", "") or "").strip()
    full_name = " ".join(part for part in [first_name, last_name] if part)
    if full_name:
        return full_name
    username = (getattr(entity, "username", "") or "").strip()
    if username:
        return f"@{username.lstrip('@')}"
    return str(getattr(entity, "id", "unknown"))


def _entity_to_registry_row(entity: Any) -> dict[str, Any] | None:
    token = entity_to_target_token(entity, prefer_username=False)
    if not token or not token.lstrip("-").isdigit():
        return None
    chat_id = int(token)
    username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
    entity_cls = entity.__class__.__name__.lower()
    chat_type = "unknown"
    if "channel" in entity_cls:
        if bool(getattr(entity, "megagroup", False)):
            chat_type = "supergroup"
        else:
            chat_type = "channel"
    elif "chat" in entity_cls:
        chat_type = "group"
    return {
        "chat_id": chat_id,
        "title": _format_entity_title(entity),
        "username": username,
        "chat_type": chat_type,
        "source": "web_sync",
    }


def _resolve_bot_chat_ref(token: str, entity: Any) -> int | str:
    canonical = entity_to_target_token(entity) or token.strip()
    if canonical.lstrip("-").isdigit():
        return int(canonical)
    return canonical


def _require_bot_token(bot_config: dict[str, Any]) -> str:
    token = str(bot_config.get("bot_token") or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 bot_token，请先在 Bot 设置中完善",
        )
    return token


async def _check_bot_manage_status(
    bot: Bot,
    *,
    bot_user_id: int,
    chat_ref: int | str,
) -> tuple[bool, str, str | None]:
    try:
        member = await bot.get_chat_member(
            chat_id=chat_ref,
            user_id=bot_user_id,
            read_timeout=_BOT_API_TIMEOUT_SECONDS,
            write_timeout=_BOT_API_TIMEOUT_SECONDS,
            connect_timeout=_BOT_API_TIMEOUT_SECONDS,
            pool_timeout=_BOT_API_TIMEOUT_SECONDS,
        )
    except TelegramError as exc:
        return False, "unknown", str(exc)
    member_status = str(getattr(member, "status", "") or "").lower()
    return member_status in _BOT_MANAGE_STATUSES, member_status or "unknown", None


async def _filter_manageable_targets(
    config: dict[str, Any],
    bot_config: dict[str, Any],
    targets: list[tuple[str, Any]],
    *,
    strict_single: bool,
) -> list[tuple[str, Any]]:
    bot_token = _require_bot_token(bot_config)
    manageable: list[tuple[str, Any]] = []
    rejected: list[str] = []
    status_updates: list[dict[str, Any]] = []

    async with Bot(token=bot_token) as bot:
        me = await bot.get_me(
            read_timeout=_BOT_API_TIMEOUT_SECONDS,
            write_timeout=_BOT_API_TIMEOUT_SECONDS,
            connect_timeout=_BOT_API_TIMEOUT_SECONDS,
            pool_timeout=_BOT_API_TIMEOUT_SECONDS,
        )
        bot_user_id = int(getattr(me, "id"))
        for token, entity in targets:
            chat_ref = _resolve_bot_chat_ref(token, entity)
            ok, member_status, reason = await _check_bot_manage_status(
                bot,
                bot_user_id=bot_user_id,
                chat_ref=chat_ref,
            )
            row = _entity_to_registry_row(entity)
            if row is not None:
                row["bot_status"] = member_status
                row["bot_can_manage"] = ok
                row["verified_at"] = int(time.time())
                row["verified_by"] = _VERIFIED_BY_GET_CHAT_MEMBER
                status_updates.append(row)
            if ok:
                manageable.append((token, entity))
                continue
            detail = f"{token}（状态: {member_status}"
            if reason:
                detail += f", 原因: {reason}"
            detail += "）"
            rejected.append(detail)

    if status_updates:
        try:
            _update_registry_manage_status(config, status_updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("target_registry_status_update_failed count=%s error=%s", len(status_updates), exc)

    if manageable:
        if rejected:
            logger.info("target_manage_filter_partial selected=%s rejected=%s", len(manageable), len(rejected))
        return manageable

    if strict_single and rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"目标群组不可维护：{'; '.join(rejected)}。请确认 Bot 在该群组中且具备管理员权限",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "未发现 Bot 可维护的目标群组/频道。"
            "请确认 Bot 已加入目标群并具备管理员权限，或在 Bot 设置中手动配置 target_chat_ids"
        ),
    )


async def _resolve_targets(
    client: TelegramClient,
    config: dict[str, Any],
    bot_config: dict[str, Any],
    *,
    requested_target: str | None = None,
) -> list[tuple[str, Any]]:
    if requested_target:
        targets = await _resolve_target_entities(client, [requested_target])
        return await _filter_manageable_targets(config, bot_config, targets, strict_single=True)

    tokens = get_target_chat_tokens(bot_config)
    if tokens:
        resolved = await _resolve_target_entities(client, tokens)
        return await _filter_manageable_targets(config, bot_config, resolved, strict_single=False)

    registry_tokens = _registry_tokens(config, manageable_only=False)
    if registry_tokens:
        resolved = await _resolve_target_entities_best_effort(client, registry_tokens)
        return await _filter_manageable_targets(config, bot_config, resolved, strict_single=False)

    targets = await discover_dialog_targets(client)
    if targets:
        return await _filter_manageable_targets(config, bot_config, targets, strict_single=False)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "未配置 target_chat_ids，且注册表/自动发现均无目标；"
            "请确认 Bot 已加入目标群并具备管理员权限，再在群里触发一次消息或成员变更"
        ),
    )


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


async def _send_command(command: str, *, requested_target: str | None = None) -> int:
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    bot_config = get_bot_config(config)
    try:
        async with open_web_client(
            bot_config,
            unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
            bot_session_detail="当前会话为 Bot 账号，无法执行维护指令",
            connect_error_detail="连接 Telethon 会话失败",
        ) as client:
            targets = await _resolve_targets(client, config, bot_config, requested_target=requested_target)
            failed: list[str] = []
            sent = 0
            for token, entity in targets:
                try:
                    await client.send_message(entity, command)
                    sent += 1
                except RPCError as exc:
                    logger.warning("send_command_failed target=%s error=%s", token, exc)
                    failed.append(token)
            if failed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"部分目标发送失败：成功 {sent}/{len(targets)}，失败 {','.join(failed)}",
                )
            return sent
    except RPCError as exc:
        logger.warning("send_command_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发送命令失败") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("send_command_failed error=%s", exc)
        raise map_telethon_exception(exc, default_detail="发送命令失败") from exc


@router.get("/targets")
async def list_targets(
    _: Annotated[str, Depends(get_current_user)],
    refresh: bool = False,
) -> dict[str, Any]:
    """List manageable targets for maintenance tools."""
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    bot_config = get_bot_config(config)

    if refresh:
        try:
            async with open_web_client(
                bot_config,
                unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
                bot_session_detail="当前会话为 Bot 账号，无法执行维护指令",
                connect_error_detail="连接 Telethon 会话失败",
            ) as client:
                discovered = await discover_dialog_targets(client, limit=500)
                rows: list[dict[str, Any]] = []
                for _, entity in discovered:
                    row = _entity_to_registry_row(entity)
                    if row is not None:
                        rows.append(row)
                synced = _sync_registry_rows(config, rows)
                logger.info("target_registry_sync refreshed=%s discovered=%s", synced, len(discovered))
        except HTTPException:
            raise
        except Exception as exc:
            raise map_telethon_exception(exc, default_detail="刷新目标群组失败") from exc

    registry_rows = _read_registry_rows(config, manageable_only=False)
    if registry_rows:
        return {
            "success": True,
            "source": "registry",
            "count": len(registry_rows),
            "manageable_count": sum(1 for row in registry_rows if row.get("bot_can_manage")),
            "targets": registry_rows,
        }

    try:
        async with open_web_client(
            bot_config,
            unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
            bot_session_detail="当前会话为 Bot 账号，无法执行维护指令",
            connect_error_detail="连接 Telethon 会话失败",
        ) as client:
            configured_tokens = get_target_chat_tokens(bot_config)
            source = "configured" if configured_tokens else "auto_discovered"
            if configured_tokens:
                resolved = await _resolve_target_entities(client, configured_tokens)
            else:
                resolved = await discover_dialog_targets(client)

            if not resolved:
                return {"success": True, "source": source, "count": 0, "targets": []}

            rows: list[dict[str, Any]] = []
            for token, entity in resolved:
                rows.append(
                    {
                        "token": token,
                        "title": _format_entity_title(entity),
                        "username": str(getattr(entity, "username", "") or "").strip().lstrip("@"),
                        "chat_type": "",
                        "source": source,
                        "bot_status": "unchecked",
                        "bot_can_manage": True,
                        "verified_at": 0,
                        "verified_by": "",
                        "reason": None,
                    }
                )
            return {
                "success": True,
                "source": source,
                "count": len(rows),
                "manageable_count": len(rows),
                "targets": rows,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise map_telethon_exception(exc, default_detail="读取目标群组失败") from exc


@router.post("/fix_tag_count", response_model=MessageResponse)
async def fix_tag_count(
    _: Annotated[str, Depends(get_current_user)],
    payload: ToolExecuteRequest | None = None,
) -> MessageResponse:
    """Trigger tag count update (maps to /tag_count)."""
    config_manager = _get_config_manager()
    bot_config = get_bot_config(config_manager.get_config())
    tag_count = _get_tag_count(bot_config)
    requested_target = _resolve_target_from_payload(payload)
    sent = await _send_command(f"/tag_count {tag_count}", requested_target=requested_target)
    return MessageResponse(success=True, message=f"已向 {sent} 个目标发送补标签数量设置：{tag_count}")


@router.post("/reload_tags", response_model=MessageResponse)
async def reload_tags(
    _: Annotated[str, Depends(get_current_user)],
    payload: ToolExecuteRequest | None = None,
) -> MessageResponse:
    """Trigger tag reload/update (maps to /tag_update)."""
    requested_target = _resolve_target_from_payload(payload)
    sent = await _send_command("/tag_update", requested_target=requested_target)
    return MessageResponse(success=True, message=f"已向 {sent} 个目标发送标签更新指令")


@router.post("/reload_system", response_model=MessageResponse)
async def reload_system(
    _: Annotated[str, Depends(get_current_user)],
    payload: ToolExecuteRequest | None = None,
) -> MessageResponse:
    """Stop current tasks (maps to /tag_stop)."""
    requested_target = _resolve_target_from_payload(payload)
    sent = await _send_command("/tag_stop", requested_target=requested_target)
    return MessageResponse(success=True, message=f"已向 {sent} 个目标发送系统重置指令")
