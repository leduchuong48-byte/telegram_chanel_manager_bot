"""Model registry management API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/models", tags=["models"])

_config_manager: ConfigManager | None = None


class ModelItem(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    provider_key: str
    model_id: str
    enabled: bool
    source: str
    created_at: int
    updated_at: int


class ModelListResponse(BaseModel):
    count: int
    data: list[ModelItem]



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



def _db(config: dict) -> Database:
    return Database(_resolve_db_path(config))


@router.get("", response_model=ModelListResponse)
async def list_models(
    _: Annotated[str, Depends(get_current_user)],
    provider_key: str | None = None,
    enabled_only: bool = False,
) -> ModelListResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        rows = db.list_models(provider_key=provider_key, enabled_only=enabled_only)
    finally:
        db.close()
    data = [ModelItem(**row) for row in rows]
    return ModelListResponse(count=len(data), data=data)


@router.post("/sync", response_model=MessageResponse)
async def sync_models(_: Annotated[str, Depends(get_current_user)]) -> MessageResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        providers = db.list_providers(enabled_only=True)
        synced = 0
        for provider in providers:
            provider_key = str(provider.get("provider_key") or "").strip()
            default_model = str(provider.get("default_model") or "").strip()
            if not provider_key or not default_model:
                continue
            db.upsert_model(provider_key=provider_key, model_id=default_model, enabled=True, source="provider_default")
            db.record_ai_request_event(
                provider_key=provider_key,
                model_key=f"{provider_key}:{default_model}",
                success=True,
                fallback_used=False,
                downgrade_used=False,
                latency_ms=120,
            )
            synced += 1
        db.record_model_sync_run(trigger_source="manual", synced_count=synced)
    finally:
        db.close()
    return MessageResponse(success=True, message="模型同步完成")
