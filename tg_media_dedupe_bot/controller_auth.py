from __future__ import annotations

from dataclasses import dataclass

_VALID_ROLES = {"owner", "admin", "operator", "readonly"}


@dataclass(frozen=True)
class ControllerPolicy:
    allowed_ids: set[int]
    primary_id: int
    auto_bind_legacy: bool
    roles_by_user_id: dict[int, str]


def _parse_int_id(value: object) -> int | None:
    raw = str(value or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        return None
    return int(raw)


def _normalize_role(value: object) -> str:
    role = str(value or "operator").strip().lower() or "operator"
    if role not in _VALID_ROLES:
        return "operator"
    return role


def can_run_command(role: str, category: str) -> bool:
    normalized_role = _normalize_role(role)
    normalized_category = str(category or "query").strip().lower() or "query"

    if normalized_role == "owner":
        return True
    if normalized_role == "admin":
        return normalized_category in {"query", "config", "dangerous", "system"}
    if normalized_role == "operator":
        return normalized_category in {"query", "config"}
    return normalized_category == "query"


def resolve_controller_policy(
    *,
    controller_rows: list[dict[str, int | str | bool]],
    legacy_controller_id: object,
    current_user_id: int,
) -> ControllerPolicy:
    enabled_ids: list[int] = []
    primary_id: int | None = None
    roles_by_user_id: dict[int, str] = {}

    for row in controller_rows:
        if not bool(row.get("enabled", False)):
            continue
        uid = _parse_int_id(row.get("user_id"))
        if uid is None:
            continue
        enabled_ids.append(uid)
        roles_by_user_id[uid] = _normalize_role(row.get("role"))
        if primary_id is None and bool(row.get("is_primary", False)):
            primary_id = uid

    if enabled_ids:
        unique_enabled = sorted(set(enabled_ids))
        if primary_id is None:
            primary_id = unique_enabled[0]
        return ControllerPolicy(
            allowed_ids=set(unique_enabled),
            primary_id=primary_id,
            auto_bind_legacy=False,
            roles_by_user_id=roles_by_user_id,
        )

    legacy_id = _parse_int_id(legacy_controller_id)
    if legacy_id is not None:
        return ControllerPolicy(
            allowed_ids={legacy_id},
            primary_id=legacy_id,
            auto_bind_legacy=False,
            roles_by_user_id={legacy_id: "owner"},
        )

    current = int(current_user_id)
    return ControllerPolicy(
        allowed_ids={current},
        primary_id=current,
        auto_bind_legacy=True,
        roles_by_user_id={current: "owner"},
    )
