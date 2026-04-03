"""Media filter settings API."""

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse

router = APIRouter(prefix="/api/filters", tags=["media_filters"])

# Global config manager instance
_config_manager: ConfigManager | None = None


class MediaFilterSettings(BaseModel):
    size_limit_mb: float = 0
    duration_limit_min: float = 0
    media_types: list[str] = Field(default_factory=list)
    filter_mode: str = "off"


class MediaFilterUpdateRequest(MediaFilterSettings):
    target: str | None = None


class MediaFilterMeta(BaseModel):
    scope: str = "default"
    target: str | None = None
    has_override: bool = False


class MediaFilterResponse(BaseModel):
    data: MediaFilterSettings
    meta: MediaFilterMeta


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def _get_config_manager() -> ConfigManager:
    """Get the config manager instance."""
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _normalize_payload(raw: dict[str, Any]) -> MediaFilterSettings:
    size_limit = raw.get("size_limit_mb", 0)
    duration_limit = raw.get("duration_limit_min", 0)
    filter_mode = str(raw.get("filter_mode", "off")).strip().lower()
    media_types_raw = raw.get("media_types", [])

    try:
        size_value = float(size_limit)
    except (TypeError, ValueError):
        size_value = 0
    if size_value < 0:
        size_value = 0

    try:
        duration_value = float(duration_limit)
    except (TypeError, ValueError):
        duration_value = 0
    if duration_value < 0:
        duration_value = 0

    if filter_mode not in {"blacklist", "whitelist"}:
        filter_mode = "off"

    allowed_types = {"video", "audio", "photo", "document", "text"}
    media_types: list[str] = []
    if isinstance(media_types_raw, list):
        for item in media_types_raw:
            if not isinstance(item, str):
                continue
            normalized = item.strip().lower()
            if normalized in allowed_types and normalized not in media_types:
                media_types.append(normalized)

    return MediaFilterSettings(
        size_limit_mb=size_value,
        duration_limit_min=duration_value,
        media_types=media_types,
        filter_mode=filter_mode,
    )


def _empty_storage() -> dict[str, Any]:
    return {
        "default": MediaFilterSettings().model_dump(),
        "overrides": {},
    }


def _normalize_storage(raw: Any) -> dict[str, Any]:
    storage = _empty_storage()
    if not isinstance(raw, dict):
        return storage

    has_nested_shape = isinstance(raw.get("default"), dict) or isinstance(raw.get("overrides"), dict)
    if not has_nested_shape:
        storage["default"] = _normalize_payload(raw).model_dump()
        return storage

    storage["default"] = _normalize_payload(raw.get("default", {})).model_dump()
    overrides: dict[str, Any] = {}
    raw_overrides = raw.get("overrides", {})
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            token = str(key or "").strip()
            if not token or not isinstance(value, dict):
                continue
            normalized = _normalize_payload(value).model_dump(exclude_none=True)
            overrides[token] = normalized
    storage["overrides"] = overrides
    return storage


def _resolve_settings_for_target(storage: dict[str, Any], target: str | None) -> tuple[MediaFilterSettings, bool]:
    default_settings = _normalize_payload(storage.get("default", {}))
    token = str(target or "").strip()
    if not token:
        return default_settings, False

    overrides = storage.get("overrides", {})
    if not isinstance(overrides, dict):
        return default_settings, False

    raw_override = overrides.get(token)
    if not isinstance(raw_override, dict):
        return default_settings, False

    merged = default_settings.model_dump()
    merged.update(_normalize_payload(raw_override).model_dump())
    return MediaFilterSettings(**merged), True


@router.get("/media", response_model=MediaFilterResponse)
async def get_media_filters(
    _: Annotated[str, Depends(get_current_user)],
    target: str | None = None,
) -> MediaFilterResponse:
    """Get persisted media filter settings."""
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    raw = config.get("web_media_filters", {}) if isinstance(config, dict) else {}
    storage = _normalize_storage(raw)
    normalized, has_override = _resolve_settings_for_target(storage, target)
    scope = "target" if str(target or "").strip() else "default"
    return MediaFilterResponse(
        data=normalized,
        meta=MediaFilterMeta(scope=scope, target=(str(target).strip() or None) if target is not None else None, has_override=has_override),
    )


@router.post("/media", response_model=MessageResponse)
async def update_media_filters(
    request: MediaFilterUpdateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Update persisted media filter settings and trigger reload."""
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())

    storage = _normalize_storage(new_config.get("web_media_filters", {}))
    normalized = _normalize_payload(request.model_dump())
    target = str(request.target or "").strip()
    if target:
        overrides = storage.setdefault("overrides", {})
        overrides[target] = normalized.model_dump()
    else:
        storage["default"] = normalized.model_dump()
    new_config["web_media_filters"] = storage

    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reload_message,
        )

    return MessageResponse(success=True, message=message)
