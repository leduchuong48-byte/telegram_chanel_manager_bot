"""Chat effective state API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.chat_effective_state import build_chat_effective_summary
from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/chat_effective_state", tags=["chat_effective_state"])

_config_manager: ConfigManager | None = None


def set_config_manager(manager: ConfigManager) -> None:
    global _config_manager
    _config_manager = manager


def _get_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _resolve_db_path(config: dict[str, Any]) -> Path:
    database = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(database, dict):
        database = {}
    raw = str(database.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _load_chat_settings(db: Database, chat_id: int) -> dict[str, object]:
    settings: dict[str, object] = {}
    for key in ["dry_run", "delete_duplicates", "media_blacklist"]:
        value = db.get_setting(f"chat:{chat_id}:{key}")
        settings[key] = value
    return settings


def _load_summary_for_chat(*, db: Database, bot_config: dict[str, Any], managed_chat: dict[str, Any]) -> dict[str, Any]:
    chat_id = int(managed_chat.get("chat_id", 0))
    chat_settings = _load_chat_settings(db, chat_id)
    return build_chat_effective_summary(
        chat_id=chat_id,
        bot_config=bot_config,
        chat_settings=chat_settings,
        managed_chat=managed_chat,
        title=str(managed_chat.get("title") or ""),
        username=str(managed_chat.get("username") or ""),
        chat_type=str(managed_chat.get("chat_type") or ""),
    )


@router.get("/chats")
async def list_chat_effective_states(
    _: Annotated[str, Depends(get_current_user)],
    limit: int = Query(default=200, ge=1, le=2000),
    active_only: bool = True,
) -> dict[str, Any]:
    manager = _get_config_manager()
    config = manager.get_config()
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}

    db_path = _resolve_db_path(config)
    db = Database(db_path)
    try:
        chats = db.list_managed_chats(active_only=active_only, manageable_only=False, limit=limit)
        items = [_load_summary_for_chat(db=db, bot_config=bot_config, managed_chat=chat) for chat in chats]
    finally:
        db.close()

    return {
        "success": True,
        "count": len(items),
        "items": items,
    }


@router.get("/events")
async def list_chat_deletion_events(
    _: Annotated[str, Depends(get_current_user)],
    chat_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    event_type: str | None = None,
) -> dict[str, Any]:
    manager = _get_config_manager()
    config = manager.get_config()
    db_path = _resolve_db_path(config)
    db = Database(db_path)
    try:
        items = db.list_deletion_events(chat_id=chat_id, limit=limit, event_type=event_type)
    finally:
        db.close()

    return {
        "success": True,
        "count": len(items),
        "items": items,
    }


@router.get("/chats/{chat_id}")
async def get_chat_effective_state(
    chat_id: int,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    manager = _get_config_manager()
    config = manager.get_config()
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}

    db_path = _resolve_db_path(config)
    db = Database(db_path)
    try:
        chats = db.list_managed_chats(active_only=False, manageable_only=False, limit=5000)
        selected = next((chat for chat in chats if int(chat.get("chat_id", 0)) == int(chat_id)), None)
        if selected is None:
            selected = {
                "chat_id": int(chat_id),
                "title": "",
                "username": "",
                "chat_type": "",
                "bot_status": "unknown",
                "bot_can_manage": False,
            }
        item = _load_summary_for_chat(db=db, bot_config=bot_config, managed_chat=selected)
    finally:
        db.close()

    return {
        "success": True,
        "item": item,
    }
