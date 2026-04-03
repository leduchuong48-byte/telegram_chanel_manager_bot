"""Forwarding rules management API."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse

router = APIRouter(prefix="/api/forwarding", tags=["forwarding"])

_config_manager: ConfigManager | None = None


class ForwardingRule(BaseModel):
    source_id: int
    target_id: int
    type: str = Field(..., pattern="^(user_mode|bot_mode)$")
    options: dict[str, Any] = Field(default_factory=dict)


class ForwardingRulesResponse(BaseModel):
    data: list[ForwardingRule]


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


def _get_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    rules = config.get("forwarding_rules", []) if isinstance(config, dict) else []
    if isinstance(rules, list):
        return [rule for rule in rules if isinstance(rule, dict)]
    return []


@router.get("", response_model=ForwardingRulesResponse)
async def list_rules(
    _: str = Depends(get_current_user),
) -> ForwardingRulesResponse:
    """List forwarding rules."""
    config_manager = _get_config_manager()
    rules = _get_rules(config_manager.get_config())
    return ForwardingRulesResponse(data=rules)


@router.post("", response_model=MessageResponse)
async def upsert_rule(
    payload: ForwardingRule,
    _: str = Depends(get_current_user),
) -> MessageResponse:
    """Add or update a forwarding rule."""
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())

    rules = _get_rules(new_config)
    updated = False
    for index, rule in enumerate(rules):
        if rule.get("source_id") == payload.source_id:
            rules[index] = payload.model_dump()
            updated = True
            break

    if not updated:
        rules.append(payload.model_dump())

    new_config["forwarding_rules"] = rules
    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reload_message)

    return MessageResponse(success=True, message=message)


@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_rule(
    source_id: int,
    _: str = Depends(get_current_user),
) -> MessageResponse:
    """Delete a forwarding rule by source_id."""
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())
    rules = _get_rules(new_config)

    filtered = [rule for rule in rules if rule.get("source_id") != source_id]
    if len(filtered) == len(rules):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到对应规则")

    new_config["forwarding_rules"] = filtered
    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reload_message)

    return MessageResponse(success=True, message=message)
