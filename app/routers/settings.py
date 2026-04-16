"""Settings API routes."""

import copy
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.core.telethon_runtime import get_target_chat_tokens, normalize_target_chat_tokens
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/settings", tags=["settings"])

TOKEN_MASK = "*****"
_BASE_DIR = Path(__file__).resolve().parents[2]
_PROVIDER_SECRETS_DIR = _BASE_DIR / "data" / "provider_secrets"

# Global config manager instance
_config_manager: ConfigManager | None = None


class BotSettingsResponse(BaseModel):
    data: dict[str, Any]


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


def _mask_token(value: str | None) -> str:
    if not value:
        return ""
    return TOKEN_MASK


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()




def _resolve_db_path(config: dict[str, Any]) -> Path:
    db_cfg = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(db_cfg, dict):
        db_cfg = {}
    raw = str(db_cfg.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _provider_secret_path(provider_key: str) -> Path:
    key = str(provider_key or "").strip().lower()
    return _PROVIDER_SECRETS_DIR / f"{key}.json"


def _read_provider_secret(provider_key: str) -> dict[str, Any]:
    path = _provider_secret_path(provider_key)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_provider_secret(*, provider_key: str, api_key: str, base_url: str, model: str) -> None:
    _PROVIDER_SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _provider_secret_path(provider_key)
    payload = {
        "provider_key": str(provider_key).strip().lower(),
        "api_key": str(api_key).strip(),
        "base_url": str(base_url).strip(),
        "model": str(model).strip(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass

@router.get("/bot", response_model=BotSettingsResponse)
async def get_bot_settings(
    _: Annotated[str, Depends(get_current_user)],
) -> BotSettingsResponse:
    """Get bot settings for admin panel."""
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}

    data = {
        "bot_token": _mask_token(bot_config.get("bot_token")),
        "api_id": bot_config.get("api_id", ""),
        "api_hash": bot_config.get("api_hash", ""),
        "target_chat_id": bot_config.get("target_chat_id", ""),
        "target_chat_ids": get_target_chat_tokens(bot_config),
        "target_chat_ids_text": "\n".join(get_target_chat_tokens(bot_config)),
        "web_tg_session": bot_config.get("web_tg_session", ""),
        "admin_id": bot_config.get("admin_id", ""),
    }
    return BotSettingsResponse(data=data)


@router.post("/bot", response_model=MessageResponse)
async def update_bot_settings(
    bot_token: Annotated[str | None, Form()] = None,
    api_id: Annotated[str | None, Form()] = None,
    api_hash: Annotated[str | None, Form()] = None,
    target_chat_id: Annotated[str | None, Form()] = None,
    target_chat_ids: Annotated[str | None, Form()] = None,
    web_tg_session: Annotated[str | None, Form()] = None,
    admin_id: Annotated[str | None, Form()] = None,
    _: Annotated[str, Depends(get_current_user)] = None,
) -> MessageResponse:
    """Update bot settings and trigger reload."""
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())

    bot_config = new_config.get("bot")
    if not isinstance(bot_config, dict):
        bot_config = {}
        new_config["bot"] = bot_config

    token_value = _clean(bot_token)
    if token_value and token_value != TOKEN_MASK:
        bot_config["bot_token"] = token_value
    elif bot_token is not None and token_value == "":
        bot_config["bot_token"] = ""

    if api_id is not None:
        bot_config["api_id"] = _clean(api_id)
    if api_hash is not None:
        bot_config["api_hash"] = _clean(api_hash)
    if target_chat_ids is not None:
        tokens = normalize_target_chat_tokens(_clean(target_chat_ids))
        bot_config["target_chat_ids"] = tokens
        bot_config["target_chat_id"] = tokens[0] if tokens else ""
    elif target_chat_id is not None:
        bot_config["target_chat_id"] = _clean(target_chat_id)
        bot_config["target_chat_ids"] = normalize_target_chat_tokens(bot_config["target_chat_id"])
    if web_tg_session is not None:
        bot_config["web_tg_session"] = _clean(web_tg_session)
    if admin_id is not None:
        bot_config["admin_id"] = _clean(admin_id)

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


class LlmSettingsResponse(BaseModel):
    data: dict[str, Any]


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings(
    _: Annotated[str, Depends(get_current_user)],
) -> LlmSettingsResponse:
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    db_path = _resolve_db_path(config)
    db = Database(db_path)
    try:
        provider = db.get_provider(provider_key="local-ai")
    finally:
        db.close()

    if provider is None:
        provider = {
            "provider_key": "local-ai",
            "base_url": "",
            "default_model": "",
            "use_responses_mode": "auto",
            "enabled": True,
        }
    secret = _read_provider_secret("local-ai")
    data = {
        "provider_key": provider.get("provider_key", "local-ai"),
        "base_url": provider.get("base_url", ""),
        "model": provider.get("default_model", ""),
        "use_responses_mode": provider.get("use_responses_mode", "auto"),
        "enabled": bool(provider.get("enabled", True)),
        "api_key": TOKEN_MASK if secret.get("api_key") else "",
    }
    return LlmSettingsResponse(data=data)


@router.post("/llm", response_model=MessageResponse)
async def update_llm_settings(
    provider_key: Annotated[str | None, Form()] = None,
    base_url: Annotated[str | None, Form()] = None,
    api_key: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    use_responses_mode: Annotated[str | None, Form()] = None,
    enabled: Annotated[str | None, Form()] = None,
    _: Annotated[str, Depends(get_current_user)] = None,
) -> MessageResponse:
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    db_path = _resolve_db_path(config)

    key = _clean(provider_key) or "local-ai"
    key = key.lower()
    next_base_url = _clean(base_url)
    next_model = _clean(model)
    mode = _clean(use_responses_mode) or "auto"
    enabled_bool = str(enabled or "1").strip().lower() not in {"0", "false", "off"}

    db = Database(db_path)
    try:
        existing = db.get_provider(provider_key=key)
        if existing is None:
            db.upsert_provider(
                provider_key=key,
                display_name=key,
                provider_type="openai_compatible",
                base_url=next_base_url,
                enabled=enabled_bool,
                use_responses_mode=mode,
                default_model=next_model,
            )
        else:
            db.upsert_provider(
                provider_key=key,
                display_name=str(existing.get("display_name") or key),
                provider_type=str(existing.get("provider_type") or "openai_compatible"),
                base_url=next_base_url or str(existing.get("base_url") or ""),
                enabled=enabled_bool,
                use_responses_mode=mode,
                default_model=next_model or str(existing.get("default_model") or ""),
            )
        if next_model:
            db.upsert_model(provider_key=key, model_id=next_model, enabled=True, source="settings")
    finally:
        db.close()

    api_key_value = _clean(api_key)
    if api_key_value and api_key_value != TOKEN_MASK:
        _write_provider_secret(
            provider_key=key,
            api_key=api_key_value,
            base_url=next_base_url,
            model=next_model,
        )

    return MessageResponse(success=True, message="LLM 设置已更新")
