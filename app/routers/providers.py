"""LLM providers management API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.services import runtime_policy
from app.services.ai_router import route_ai_request
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


def _coerce_int(value: object, *, default: int, minimum: int) -> int:
    try:
        result = int(str(value).strip())
    except Exception:  # noqa: BLE001
        result = default
    return max(minimum, result)


def _normalize_fallbacks(value: object) -> list[str]:
    items: list[str]
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(',')]
    else:
        items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _load_runtime_policy(config: dict) -> tuple[int, int, list[str]]:
    ai_runtime = config.get('ai_runtime', {}) if isinstance(config, dict) else {}
    if not isinstance(ai_runtime, dict):
        ai_runtime = {}
    timeout_ms = _coerce_int(ai_runtime.get('timeout_ms', 10000), default=10000, minimum=1000)
    retry_count = _coerce_int(ai_runtime.get('retry_count', 1), default=1, minimum=0)
    fallback_provider_keys = _normalize_fallbacks(ai_runtime.get('fallback_provider_keys'))
    return timeout_ms, retry_count, fallback_provider_keys


class _HttpProviderAdapter:
    def __init__(self, db: Database) -> None:
        self._db = db

    def generate(self, *, provider_key: str, model_key: str, prompt: str, timeout_ms: int) -> dict:
        provider = self._db.get_provider(provider_key=provider_key)
        if provider is None:
            raise RuntimeError(f'provider_missing:{provider_key}')

        base_url = str(provider.get('base_url') or '').strip()
        if not base_url:
            raise RuntimeError(f'provider_base_url_missing:{provider_key}')
        if base_url.lower().startswith('fail://'):
            raise TimeoutError(f'provider_timeout:{provider_key}')

        secret = _read_provider_secret(provider_key)
        api_key = str(secret.get('api_key') or '').strip()
        if not api_key:
            return {'text': 'ok_no_api_key_skip', 'latency_ms': 0}

        endpoint = f"{base_url.rstrip('/')}/models"
        request = Request(
            endpoint,
            method='GET',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Accept': 'application/json',
                'User-Agent': 'channel-manager-bot/ai-provider-test',
            },
        )

        started = time.time()
        timeout_seconds = max(1.0, float(timeout_ms) / 1000.0)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
                status_code = int(getattr(response, 'status', 200) or 200)
                if status_code >= 400:
                    raise RuntimeError(f'provider_http_status:{status_code}')
                response.read()
        except TimeoutError:
            raise
        except HTTPError as exc:
            raise RuntimeError(f'provider_http_status:{int(exc.code)}') from exc
        except URLError as exc:
            raise RuntimeError(f'provider_network_error:{exc.reason}') from exc

        latency_ms = int((time.time() - started) * 1000)
        return {'text': 'ok_http', 'latency_ms': max(1, latency_ms)}


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


def _read_provider_secret(provider_key: str) -> dict:
    path = _provider_secret_path(provider_key)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


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
    config = manager.get_config()
    normalized_key = _normalize_provider_key(provider_key)
    db = _db(config)
    try:
        provider = db.get_provider(provider_key=normalized_key)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider 不存在")

        timeout_ms, retry_count, configured_fallbacks = _load_runtime_policy(config)
        provider_chain = runtime_policy.resolve_provider_chain(
            primary_provider_key=normalized_key,
            configured_fallbacks=configured_fallbacks,
        )
        provider_chain = [key for key in provider_chain if db.get_provider(provider_key=key) is not None]
        if not provider_chain:
            provider_chain = [normalized_key]

        default_model = str(provider.get("default_model") or "").strip()
        effective_model_key = f"{normalized_key}:{default_model}" if default_model else f"{normalized_key}:_none"

        route_ai_request(
            db=db,
            adapter=_HttpProviderAdapter(db),
            provider_chain=provider_chain,
            model_key=effective_model_key,
            prompt="provider_connection_test",
            retry_count=retry_count,
            timeout_ms=timeout_ms,
        )

        db.mark_provider_test_result(provider_key=normalized_key, status="ok")
    finally:
        db.close()
    return MessageResponse(success=True, message="连接测试完成")


@router.post("/{provider_key}/probe", response_model=MessageResponse)
async def probe_provider_capabilities(
    provider_key: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    manager = _get_config_manager()
    config = manager.get_config()
    normalized_key = _normalize_provider_key(provider_key)
    db = _db(config)
    try:
        provider = db.get_provider(provider_key=normalized_key)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider 不存在")

        timeout_ms, retry_count, configured_fallbacks = _load_runtime_policy(config)
        provider_chain = runtime_policy.resolve_provider_chain(
            primary_provider_key=normalized_key,
            configured_fallbacks=configured_fallbacks,
        )
        provider_chain = [key for key in provider_chain if db.get_provider(provider_key=key) is not None]
        if not provider_chain:
            provider_chain = [normalized_key]

        default_model = str(provider.get("default_model") or "").strip()
        effective_model_key = f"{normalized_key}:{default_model}" if default_model else f"{normalized_key}:_none"

        route_ai_request(
            db=db,
            adapter=_HttpProviderAdapter(db),
            provider_chain=provider_chain,
            model_key=effective_model_key,
            prompt="provider_capability_probe",
            retry_count=retry_count,
            timeout_ms=timeout_ms,
        )

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
    finally:
        db.close()
    return MessageResponse(success=True, message="能力探测完成")
