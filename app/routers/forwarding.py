"""Forwarding rules management API."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.services.forwarding_runtime import UserForwardingRuntime

router = APIRouter(prefix="/api/forwarding", tags=["forwarding"])

_config_manager: ConfigManager | None = None
_runtime: UserForwardingRuntime | None = None


class ForwardingRule(BaseModel):
    rule_id: str | None = None
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


def set_runtime(runtime: UserForwardingRuntime) -> None:
    global _runtime
    _runtime = runtime


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
        return [_normalize_rule_data(rule) for rule in rules if isinstance(rule, dict)]
    return []


def _rule_id(rule: dict[str, Any]) -> str:
    return f"{str(rule.get('type') or '').strip()}:{int(rule.get('source_id'))}:{int(rule.get('target_id'))}"


def _normalize_rule_data(rule: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(rule)
    normalized["source_id"] = int(normalized.get("source_id"))
    normalized["target_id"] = int(normalized.get("target_id"))
    normalized["type"] = str(normalized.get("type") or "").strip()
    normalized["options"] = dict(normalized.get("options") or {})
    normalized["rule_id"] = _rule_id(normalized)
    return normalized


def _upsert_rule_data(rules: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_payload = _normalize_rule_data(payload)
    result = [_normalize_rule_data(rule) for rule in rules]
    for index, rule in enumerate(result):
        if rule["rule_id"] == normalized_payload["rule_id"]:
            result[index] = normalized_payload
            return result
    result.append(normalized_payload)
    return result


def _validate_rule_graph(rules: list[dict[str, Any]]) -> None:
    edges: dict[int, set[int]] = {}
    for raw in rules:
        rule = _normalize_rule_data(raw)
        source_id = int(rule["source_id"])
        target_id = int(rule["target_id"])
        if source_id == target_id:
            raise ValueError("source_and_target_must_differ")
        edges.setdefault(source_id, set()).add(target_id)

    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(node: int) -> None:
        if node in visiting:
            raise ValueError("forwarding_cycle_detected")
        if node in visited:
            return
        visiting.add(node)
        for target in edges.get(node, set()):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for source_id in edges:
        visit(source_id)


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

    rules = _upsert_rule_data(_get_rules(new_config), payload.model_dump(exclude={"rule_id"}))
    try:
        _validate_rule_graph(rules)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    new_config["forwarding_rules"] = rules
    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reload_message)

    return MessageResponse(success=True, message=message)


@router.get("/status")
async def forwarding_status(
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    if _runtime is None:
        return {"status": "unavailable", "detail": "forwarding runtime not initialized", "user_rule_count": 0}
    return _runtime.status()


@router.delete("/rule/{rule_id}", response_model=MessageResponse)
async def delete_rule_by_id(
    rule_id: str,
    _: str = Depends(get_current_user),
) -> MessageResponse:
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())
    rules = _get_rules(new_config)
    filtered = [rule for rule in rules if rule.get("rule_id") != rule_id]
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


@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_rule(
    source_id: int,
    _: str = Depends(get_current_user),
) -> MessageResponse:
    """Delete a forwarding rule by source_id."""
    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())
    rules = _get_rules(new_config)

    filtered = [rule for rule in rules if int(rule.get("source_id")) != source_id]
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
