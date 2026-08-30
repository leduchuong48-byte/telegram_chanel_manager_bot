"""Bot settings API routes."""

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.core.telethon_runtime import get_target_chat_tokens, normalize_target_chat_tokens

router = APIRouter(prefix="/api/bot_settings", tags=["bot_settings"])

TOKEN_MASK = "*****"

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


@router.get("", response_model=BotSettingsResponse)
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


@router.post("", response_model=MessageResponse)
async def update_bot_settings(
    bot_token: Annotated[str | None, Form(None)] = None,
    api_id: Annotated[str | None, Form(None)] = None,
    api_hash: Annotated[str | None, Form(None)] = None,
    target_chat_id: Annotated[str | None, Form(None)] = None,
    target_chat_ids: Annotated[str | None, Form(None)] = None,
    web_tg_session: Annotated[str | None, Form(None)] = None,
    admin_id: Annotated[str | None, Form(None)] = None,
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
