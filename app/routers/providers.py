"""LLM providers management API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/providers", tags=["providers"])

_config_manager: ConfigManager | None = None

_PROVIDER_TYPES = {
    "openai_compatible",
    "anthropic",
    "gemini",
    "ollama",
    "vllm",
    "openrouter",
}

_RESPONSES_MODES = {"off", "auto", "force_on", "force_off"}
_API_KEY_MASK = "*****"
_BASE_DIR = Path(__file__).resolve().parents[2]
_PROVIDER_SECRETS_DIR = _BASE_DIR / "data" / "provider_secrets"


class ProviderItem(BaseModel):
    provider_key: str
    display_name: str
    provider_type: str
    base_url: str
    enabled: bool
    use_responses_mode: str
    default_model: str
    last_test_status: str
    last_test_at: int
    last_probe_status: str
    last_probe_at: int
    supports_responses: bool
    capabilities_json: str
    has_api_key: bool = False
    created_at: int
    updated_at: int


class ProviderListResponse(BaseModel):
    count: int
    data: list[ProviderItem]


class ProviderCreateRequest(BaseModel):
    provider_key: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    provider_type: Literal[
        "openai_compatible",
        "anthropic",
        "gemini",
        "ollama",
        "vllm",
        "openrouter",
    ]
    base_url: str = Field(..., min_length=1)
    enabled: bool = True
    use_responses_mode: Literal["off", "auto", "force_on", "force_off"] = "auto"
    default_model: str = ""
    api_key: str | None = None


class ProviderUpdateRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    use_responses_mode: Literal["off", "auto", "force_on", "force_off"] | None = None
    default_model: str | None = None
    api_key: str | None = None



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



def _normalize_provider_key(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider_key 不能为空")
    for ch in value:
        if not (ch.isalnum() or ch in {"_", "-"}):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider_key 仅支持字母数字下划线中划线")
    return value



def _validate_type_and_mode(provider_type: str, use_responses_mode: str) -> None:
    if provider_type not in _PROVIDER_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider_type 不支持")
    if use_responses_mode not in _RESPONSES_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="use_responses_mode 不支持")


def _provider_secret_path(provider_key: str) -> Path:
    safe_key = _normalize_provider_key(provider_key)
    return _PROVIDER_SECRETS_DIR / f"{safe_key}.json"


def _has_provider_api_key(provider_key: str) -> bool:
    return _provider_secret_path(provider_key).exists()


def _write_provider_api_key(*, provider_key: str, api_key: str, base_url: str, default_model: str) -> None:
    _PROVIDER_SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _provider_secret_path(provider_key)
    payload = {
        "provider_key": _normalize_provider_key(provider_key),
        "api_key": str(api_key).strip(),
        "base_url": str(base_url).strip(),
        "model": str(default_model).strip(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _delete_provider_api_key(provider_key: str) -> None:
    path = _provider_secret_path(provider_key)
    if path.exists():
        path.unlink()


@router.get("", response_model=ProviderListResponse)
async def list_providers(
    _: Annotated[str, Depends(get_current_user)],
    enabled_only: bool = False,
) -> ProviderListResponse:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        rows = db.list_providers(enabled_only=enabled_only)
    finally:
        db.close()
    data: list[ProviderItem] = []
    for row in rows:
        payload = dict(row)
        payload["has_api_key"] = _has_provider_api_key(str(payload.get("provider_key") or ""))
        data.append(ProviderItem(**payload))
    return ProviderListResponse(count=len(data), data=data)


@router.post("", response_model=MessageResponse)
async def create_provider(
    payload: ProviderCreateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    provider_key = _normalize_provider_key(payload.provider_key)
    provider_type = str(payload.provider_type).strip()
    use_responses_mode = str(payload.use_responses_mode).strip()
    _validate_type_and_mode(provider_type, use_responses_mode)

    db = _db(manager.get_config())
    try:
        if db.get_provider(provider_key=provider_key) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="provider_key 已存在")
        base_url = str(payload.base_url).strip()
        default_model = str(payload.default_model or "").strip()
        db.upsert_provider(
            provider_key=provider_key,
            display_name=str(payload.display_name).strip(),
            provider_type=provider_type,
            base_url=base_url,
            enabled=bool(payload.enabled),
            use_responses_mode=use_responses_mode,
            default_model=default_model,
        )
        api_key = str(payload.api_key or "").strip()
        if api_key and api_key != _API_KEY_MASK:
            _write_provider_api_key(
                provider_key=provider_key,
                api_key=api_key,
                base_url=base_url,
                default_model=default_model,
            )
    finally:
        db.close()

    return MessageResponse(success=True, message="Provider 已创建")


@router.patch("/{provider_key}", response_model=MessageResponse)
async def update_provider(
    provider_key: str,
    payload: ProviderUpdateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    normalized_key = _normalize_provider_key(provider_key)
    db = _db(manager.get_config())
    try:
        existing = db.get_provider(provider_key=normalized_key)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider 不存在")

        provider_type = str(existing["provider_type"])
        mode = str(payload.use_responses_mode or existing["use_responses_mode"])
        _validate_type_and_mode(provider_type, mode)

        next_base_url = str(payload.base_url if payload.base_url is not None else existing["base_url"]).strip()
        next_default_model = str(payload.default_model if payload.default_model is not None else existing["default_model"]).strip()
        db.upsert_provider(
            provider_key=normalized_key,
            display_name=str(payload.display_name if payload.display_name is not None else existing["display_name"]).strip(),
            provider_type=provider_type,
            base_url=next_base_url,
            enabled=bool(payload.enabled if payload.enabled is not None else existing["enabled"]),
            use_responses_mode=mode,
            default_model=next_default_model,
        )

        if payload.api_key is not None:
            api_key = str(payload.api_key).strip()
            if api_key and api_key != _API_KEY_MASK:
                _write_provider_api_key(
                    provider_key=normalized_key,
                    api_key=api_key,
                    base_url=next_base_url,
                    default_model=next_default_model,
                )
            elif api_key == "":
                _delete_provider_api_key(normalized_key)
    finally:
        db.close()

    return MessageResponse(success=True, message="Provider 已更新")


@router.post("/{provider_key}/test", response_model=MessageResponse)
async def test_provider_connection(
    provider_key: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    normalized_key = _normalize_provider_key(provider_key)
    db = _db(manager.get_config())
    try:
        provider = db.get_provider(provider_key=normalized_key)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider 不存在")
        db.mark_provider_test_result(provider_key=normalized_key, status="ok")
        default_model = str(provider.get("default_model") or "").strip()
        model_key = f"{normalized_key}:{default_model}" if default_model else f"{normalized_key}:_none"
        db.record_ai_request_event(
            provider_key=normalized_key,
            model_key=model_key,
            success=True,
            fallback_used=False,
            downgrade_used=False,
            latency_ms=50,
        )
    finally:
        db.close()
    return MessageResponse(success=True, message="连接测试完成")


@router.post("/{provider_key}/probe", response_model=MessageResponse)
async def probe_provider_capabilities(
    provider_key: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    normalized_key = _normalize_provider_key(provider_key)
    db = _db(manager.get_config())
    try:
        provider = db.get_provider(provider_key=normalized_key)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider 不存在")
        supports_responses = str(provider.get("provider_type") or "") == "openai_compatible"
        capabilities_json = json.dumps(
            {
                "supports_responses": supports_responses,
                "source": "static_probe_v1",
            },
            ensure_ascii=False,
        )
        db.mark_provider_probe_result(
            provider_key=normalized_key,
            status="ok",
            supports_responses=supports_responses,
            capabilities_json=capabilities_json,
        )
        default_model = str(provider.get("default_model") or "").strip()
        model_key = f"{normalized_key}:{default_model}" if default_model else f"{normalized_key}:_none"
        db.record_ai_request_event(
            provider_key=normalized_key,
            model_key=model_key,
            success=True,
            fallback_used=False,
            downgrade_used=False,
            latency_ms=80,
        )
    finally:
        db.close()
    return MessageResponse(success=True, message="能力探测完成")
