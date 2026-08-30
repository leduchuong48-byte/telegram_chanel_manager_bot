from __future__ import annotations

from typing import Any

_MEDIA_BLACKLIST_TYPES = {"video", "audio", "photo", "text", "document"}


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _normalize_media_blacklist(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [part.strip().lower() for part in raw.split(",")]
        return sorted({item for item in items if item in _MEDIA_BLACKLIST_TYPES})
    if isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, str):
                continue
            normalized = item.strip().lower()
            if normalized in _MEDIA_BLACKLIST_TYPES:
                items.append(normalized)
        return sorted(set(items))
    return []


def build_chat_effective_summary(
    *,
    chat_id: int,
    bot_config: dict[str, Any],
    chat_settings: dict[str, object],
    managed_chat: dict[str, object],
    title: str = "",
    username: str = "",
    chat_type: str = "",
) -> dict[str, Any]:
    global_dry_run = _parse_bool(bot_config.get("dry_run"))
    if global_dry_run is None:
        global_dry_run = True

    global_delete_enabled = _parse_bool(bot_config.get("delete_duplicates"))
    if global_delete_enabled is None:
        global_delete_enabled = False

    raw_chat_dry = chat_settings.get("dry_run")
    chat_dry_run = _parse_bool(raw_chat_dry)
    dry_run = global_dry_run if chat_dry_run is None else chat_dry_run
    dry_source = "global_default" if chat_dry_run is None else "chat_override"

    raw_chat_delete = chat_settings.get("delete_duplicates")
    chat_delete_enabled = _parse_bool(raw_chat_delete)
    delete_enabled = global_delete_enabled if chat_delete_enabled is None else chat_delete_enabled
    delete_source = "global_default" if chat_delete_enabled is None else "chat_override"

    raw_blacklist = chat_settings.get("media_blacklist")
    media_blacklist = _normalize_media_blacklist(raw_blacklist)
    block_text = "text" in media_blacklist
    policy_source = "chat_override" if raw_blacklist is not None else "global_default"

    if dry_run:
        mode = "observe"
        mode_source = dry_source
        mode_reason = "dry_run_enabled"
    elif delete_enabled:
        mode = "delete"
        mode_source = delete_source
        mode_reason = "delete_enabled"
    else:
        mode = "observe"
        mode_source = delete_source
        mode_reason = "delete_disabled"

    bot_status = str(managed_chat.get("bot_status") or "unknown").strip().lower() or "unknown"
    bot_can_manage = bool(managed_chat.get("bot_can_manage", False))

    conflicts: list[str] = []
    if block_text and mode != "delete":
        conflicts.append("text_policy_without_delete")
    if mode == "delete" and not bot_can_manage:
        conflicts.append("delete_enabled_but_bot_cannot_manage")

    if not block_text:
        result = "unconfigured"
        summary = "未启用纯文本过滤策略。"
    elif mode != "delete":
        result = "matched_but_not_deleting"
        summary = "已命中文本过滤策略，但当前为观测模式，不会自动删除。"
    elif not bot_can_manage:
        result = "permission_blocked"
        summary = "删除已启用，但机器人缺少管理权限，无法执行删除。"
    else:
        result = "deleting"
        summary = "文本过滤已启用，命中后将自动删除。"

    return {
        "chat_id": int(chat_id),
        "title": str(title or ""),
        "username": str(username or ""),
        "chat_type": str(chat_type or ""),
        "policy": {
            "block_text": block_text,
            "blocked_media_types": media_blacklist,
            "source": {
                "block_text": policy_source,
                "blocked_media_types": policy_source,
            },
        },
        "enforcement": {
            "mode": mode,
            "source": mode_source,
            "reason": mode_reason,
        },
        "runtime": {
            "status": "active",
            "dry_run_legacy": dry_run,
            "source": dry_source,
        },
        "bot": {
            "status": bot_status,
            "can_manage": bot_can_manage,
            "source": "managed_chats",
        },
        "effective": {
            "result": result,
            "summary": summary,
            "conflicts": conflicts,
        },
    }
