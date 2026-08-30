"""Telegram controllers management API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/telegram-controllers", tags=["telegram_controllers"])


class ControllerItem(BaseModel):
    user_id: int
    display_name: str
    role: str
    enabled: bool
    is_primary: bool
    source: str
    created_at: int
    updated_at: int
    last_verified_at: int


class ControllerListResponse(BaseModel):
    count: int
    data: list[ControllerItem]


class ControllerCreateRequest(BaseModel):
    user_id: int
    display_name: str = ""
    role: str = "operator"
    enabled: bool = True
    is_primary: bool = False


class ControllerUpdateRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    enabled: bool | None = None


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


def _resolve_db_path(config: dict) -> Path:
    db_cfg = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(db_cfg, dict):
        db_cfg = {}
    raw = str(db_cfg.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _list_items(db: Database, *, enabled_only: bool) -> list[ControllerItem]:
    rows = db.list_telegram_controllers(enabled_only=enabled_only)
    return [ControllerItem(**row) for row in rows]


def _db(config: dict) -> Database:
    return Database(_resolve_db_path(config))


@router.get("", response_model=ControllerListResponse)
async def list_telegram_controllers(
    _: Annotated[str, Depends(get_current_user)],
    enabled_only: bool = False,
) -> ControllerListResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        data = _list_items(db, enabled_only=enabled_only)
    finally:
        db.close()
    return ControllerListResponse(count=len(data), data=data)


@router.post("", response_model=MessageResponse)
async def create_telegram_controller(
    payload: ControllerCreateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        db.upsert_telegram_controller(
            user_id=int(payload.user_id),
            display_name=str(payload.display_name or "").strip(),
            enabled=bool(payload.enabled),
            is_primary=bool(payload.is_primary),
            source="web_admin",
            role=str(payload.role or "operator"),
        )
    finally:
        db.close()
    return MessageResponse(success=True, message="控制用户已保存")


@router.patch("/{user_id}", response_model=MessageResponse)
async def update_telegram_controller(
    user_id: int,
    payload: ControllerUpdateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        existing = db.list_telegram_controllers(enabled_only=False)
        target = next((r for r in existing if int(r["user_id"]) == int(user_id)), None)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="控制用户不存在")

        display_name = target["display_name"]
        enabled = target["enabled"]
        role = target.get("role", "operator")
        if payload.display_name is not None:
            display_name = str(payload.display_name).strip()
        if payload.role is not None:
            role = str(payload.role).strip().lower() or "operator"
        if payload.enabled is not None:
            try:
                db.set_telegram_controller_enabled(user_id=int(user_id), enabled=bool(payload.enabled))
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            enabled = bool(payload.enabled)

        db.upsert_telegram_controller(
            user_id=int(user_id),
            display_name=str(display_name),
            enabled=bool(enabled),
            is_primary=bool(target["is_primary"]),
            source="web_admin",
            role=str(payload.role or "operator"),
        )
    finally:
        db.close()
    return MessageResponse(success=True, message="控制用户已更新")


@router.post("/{user_id}/make-primary", response_model=MessageResponse)
async def make_primary_controller(
    user_id: int,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        try:
            db.set_primary_telegram_controller(user_id=int(user_id))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    finally:
        db.close()
    return MessageResponse(success=True, message="已设置主控制用户")


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_telegram_controller(
    user_id: int,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        try:
            db.delete_telegram_controller(user_id=int(user_id))
        except ValueError as exc:
            detail = str(exc)
            code = status.HTTP_400_BAD_REQUEST
            if detail == "controller_user_not_found":
                code = status.HTTP_404_NOT_FOUND
            raise HTTPException(status_code=code, detail=detail) from exc
    finally:
        db.close()
    return MessageResponse(success=True, message="控制用户已删除")
