"""Shared Telethon runtime helpers for Web API routers."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import HTTPException, status

from app.core.runtime_settings import load_runtime_settings
from telethon import TelegramClient
from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden

logger = logging.getLogger(__name__)

_WEB_TELETHON_LOCK = asyncio.Lock()
_WEB_TELETHON_TIMEOUT_SECONDS = 30


def get_bot_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return normalized bot config object from global config."""
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}
    settings = load_runtime_settings(config if isinstance(config, dict) else {})
    merged = dict(bot_config)
    merged["dry_run"] = settings.dry_run
    merged["delete_duplicates"] = settings.delete_duplicates
    merged["api_id"] = settings.api_id or merged.get("api_id", "")
    merged["api_hash"] = settings.api_hash or merged.get("api_hash", "")
    merged["bot_token"] = settings.bot_token or merged.get("bot_token", "")
    merged["admin_id"] = settings.admin_id or merged.get("admin_id", "")
    merged["target_chat_ids"] = settings.target_chat_tokens
    merged["web_tg_session"] = settings.web_tg_session
    return merged


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def normalize_target_chat_tokens(value: Any) -> list[str]:
    """Normalize target chat tokens from string/list/int input."""
    if value is None:
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        parts = [part.strip() for part in re.split(r"[\s,;]+", raw) if part.strip()]
        return _dedupe_keep_order(parts)
    if isinstance(value, list):
        merged: list[str] = []
        for item in value:
            merged.extend(normalize_target_chat_tokens(item))
        return _dedupe_keep_order(merged)
    return []


def get_target_chat_tokens(bot_config: dict[str, Any]) -> list[str]:
    """
    Resolve configured target chats.

    Prefer `target_chat_ids` list; fallback to legacy `target_chat_id`.
    """
    tokens = normalize_target_chat_tokens(bot_config.get("target_chat_ids"))
    if tokens:
        return tokens
    return normalize_target_chat_tokens(bot_config.get("target_chat_id"))


def parse_target_chat_token(token: str) -> tuple[str | None, int | None]:
    """Convert single token into telethon resolve args."""
    raw = token.strip()
    if raw.lstrip("-").isdigit():
        return None, int(raw)
    return raw, None


def entity_to_target_token(entity: Any, *, prefer_username: bool = True) -> str | None:
    """Convert dialog entity into target token string."""
    if prefer_username:
        username = getattr(entity, "username", None)
        if isinstance(username, str):
            normalized = username.strip().lstrip("@")
            if normalized:
                return f"@{normalized}"

    entity_id = getattr(entity, "id", None)
    if not isinstance(entity_id, int):
        return None

    if isinstance(entity, (Channel, ChannelForbidden)):
        if entity_id < 0:
            return str(entity_id)
        return f"-100{entity_id}"
    if isinstance(entity, (Chat, ChatForbidden)):
        if entity_id < 0:
            return str(entity_id)
        return str(-entity_id)

    cls_name = entity.__class__.__name__.lower()
    if "channel" in cls_name:
        return f"-100{abs(entity_id)}"
    if "chat" in cls_name:
        return str(-abs(entity_id))
    return None


async def discover_dialog_targets(
    client: TelegramClient,
    *,
    limit: int = 200,
) -> list[tuple[str, Any]]:
    """Discover group/channel targets from current web session dialogs."""
    results: list[tuple[str, Any]] = []
    seen: set[str] = set()
    safe_limit = max(1, int(limit))
    async for dialog in client.iter_dialogs(limit=safe_limit):
        is_group = bool(getattr(dialog, "is_group", False))
        is_channel = bool(getattr(dialog, "is_channel", False))
        if not is_group and not is_channel:
            continue
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        token = entity_to_target_token(entity)
        if not token or token in seen:
            continue
        seen.add(token)
        results.append((token, entity))
    return results


def _session_db_path(raw_path: Path) -> Path:
    """Normalize Telethon session DB file path."""
    if raw_path.suffix:
        return raw_path
    return raw_path.with_suffix(".session")


def resolve_web_session_path(bot_config: dict[str, Any]) -> Path:
    """
    Resolve Web UI Telethon session path.

    Priority:
    1) bot.web_tg_session
    2) bot.tg_session (backward compatible)
    3) env WEB_TG_SESSION
    4) ./sessions/webui
    """
    raw = str(
        bot_config.get("web_tg_session")
        or bot_config.get("tg_session")
        or os.getenv("WEB_TG_SESSION", "")
        or "./sessions/webui"
    ).strip()
    path = Path(raw).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def bootstrap_web_session(bot_config: dict[str, Any], web_session_path: Path) -> None:
    """
    Bootstrap web session by copying from runtime TG_SESSION once.

    This avoids forcing a re-login when migrating from shared session to
    dedicated Web UI session.
    """
    web_db = _session_db_path(web_session_path)
    if web_db.exists():
        return

    runtime_raw = str(os.getenv("TG_SESSION", "") or "./sessions/user").strip()
    runtime_db = _session_db_path(Path(runtime_raw).expanduser())
    if not runtime_db.exists():
        return

    try:
        if runtime_db.resolve() == web_db.resolve():
            return
    except OSError:
        if str(runtime_db) == str(web_db):
            return

    try:
        _copy_if_exists(runtime_db, web_db)
        for suffix in ("-journal", "-wal", "-shm"):
            _copy_if_exists(Path(f"{runtime_db}{suffix}"), Path(f"{web_db}{suffix}"))
        logger.info("web_session_bootstrapped src=%s dst=%s", runtime_db, web_db)
    except OSError as exc:
        logger.warning("web_session_bootstrap_failed src=%s dst=%s error=%s", runtime_db, web_db, exc)


def get_api_credentials(bot_config: dict[str, Any]) -> tuple[int, str]:
    """Read and validate Telethon API credentials."""
    api_id_raw = bot_config.get("api_id") or os.getenv("TG_API_ID", "")
    api_hash = str(bot_config.get("api_hash") or os.getenv("TG_API_HASH", "")).strip()

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
    return api_id, api_hash


def map_telethon_exception(exc: Exception, *, default_detail: str) -> HTTPException:
    """Map low-level Telethon/SQLite exceptions into API-friendly errors."""
    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Telethon 会话被占用（database is locked）。"
                "请在 Bot 设置中配置 web_tg_session（建议 ./sessions/webui），或稍后重试。"
            ),
        )

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=default_detail,
    )


async def safe_disconnect(client: TelegramClient) -> None:
    """Disconnect client without raising into API stack."""
    try:
        await client.disconnect()
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
            logger.info("telethon_disconnect_skipped reason=database_locked")
            return
        logger.warning("telethon_disconnect_failed error=%s", exc)


@asynccontextmanager
async def web_telethon_lock() -> AsyncIterator[None]:
    """Serialize access to web-side Telethon session files."""
    async with _WEB_TELETHON_LOCK:
        yield


@asynccontextmanager
async def open_web_client(
    bot_config: dict[str, Any],
    *,
    unauthorized_detail: str,
    bot_session_detail: str,
    connect_error_detail: str,
) -> AsyncIterator[TelegramClient]:
    """
    Open a Web Telethon client with process-wide lock and validation.

    Locking avoids concurrent access on the same SQLite session file.
    """
    api_id, api_hash = get_api_credentials(bot_config)
    session_path = resolve_web_session_path(bot_config)
    bootstrap_web_session(bot_config, session_path)

    async with web_telethon_lock():
        client = TelegramClient(str(session_path), api_id, api_hash)
        try:
            await asyncio.wait_for(client.connect(), timeout=_WEB_TELETHON_TIMEOUT_SECONDS)
            if not await asyncio.wait_for(
                client.is_user_authorized(),
                timeout=_WEB_TELETHON_TIMEOUT_SECONDS,
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=unauthorized_detail,
                )
            me = await asyncio.wait_for(client.get_me(), timeout=_WEB_TELETHON_TIMEOUT_SECONDS)
            if getattr(me, "bot", False):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=bot_session_detail,
                )
            yield client
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, HTTPException):
                raise
            raise map_telethon_exception(exc, default_detail=connect_error_detail) from exc
        finally:
            await safe_disconnect(client)
