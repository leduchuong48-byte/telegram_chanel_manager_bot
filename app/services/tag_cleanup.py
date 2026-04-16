"""In-memory one-shot tag cleanup service."""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.services import tag_cleanup_rules

_SESSIONS: dict[str, dict[str, Any]] = {}


def _make_session_id() -> str:
    return f"cleanup_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _normalize_source_tags(tag_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [str(item.get("tag") or "") for item in tag_items]
    normalized = tag_cleanup_rules.normalize_input_tags(names)
    meta: dict[str, dict[str, Any]] = {}
    for item in tag_items:
        key = str(item.get("tag") or "").strip().lstrip("#").casefold()
        if not key:
            continue
        if key not in meta:
            meta[key] = {
                "count": int(item.get("count") or 0),
                "samples": [str(x) for x in (item.get("samples") or [])],
                "aliases": [str(x) for x in (item.get("aliases") or [])],
            }
    result: list[dict[str, Any]] = []
    for tag in normalized:
        m = meta.get(tag, {"count": 0, "samples": [], "aliases": []})
        result.append(
            {
                "tag": tag,
                "count": int(m["count"]),
                "samples": list(m["samples"]),
                "aliases": list(m["aliases"]),
            }
        )
    return result


def _suggest_action(item: dict[str, Any]) -> dict[str, Any]:
    tag = str(item.get("tag") or "").strip().casefold()
    aliases = [str(x).strip().lstrip("#").casefold() for x in (item.get("aliases") or []) if str(x).strip()]
    target = ""
    action = "keep"
    reason = "保持不变"
    category = None
    confidence = 0.65

    for alias in aliases:
        if alias and alias != tag:
            action = "rename"
            target = alias
            reason = "检测到同义标签，建议统一命名"
            confidence = 0.9
            break

    if action == "keep" and ("old" in tag or "废弃" in tag):
        action = "deprecate"
        reason = "疑似历史或废弃标签"
        confidence = 0.75

    return {
        "source_tag": tag,
        "suggested_action": action,
        "suggested_target_tag": target,
        "suggested_category": category,
        "reason": reason,
        "confidence": confidence,
        "samples": [str(x) for x in (item.get("samples") or [])],
    }


def preview_cleanup(*, source_type: str, tag_items: list[dict[str, Any]]) -> dict[str, Any]:
    session_id = _make_session_id()
    normalized_items = _normalize_source_tags(tag_items)
    suggestions = [_suggest_action(item) for item in normalized_items]
    cleaned = tag_cleanup_rules.clean_suggestions(suggestions)

    items: list[dict[str, Any]] = []
    high_conf = 0
    for idx, suggestion in enumerate(cleaned, 1):
        if float(suggestion.get("confidence") or 0.0) >= 0.85:
            high_conf += 1
        items.append(
            {
                "item_id": f"item_{idx:04d}",
                **suggestion,
                "decision": "pending",
                "final_action": None,
                "final_target_tag": None,
                "final_category": None,
            }
        )

    _SESSIONS[session_id] = {
        "session_id": session_id,
        "source_type": source_type,
        "status": "preview_ready",
        "created_at": int(time.time()),
        "items": items,
    }

    return {
        "session_id": session_id,
        "status": "preview_ready",
        "summary": {
            "total_input_tags": len(normalized_items),
            "total_suggestions": len(items),
            "high_confidence_count": high_conf,
        },
        "items": items,
    }


def apply_cleanup(*, session_id: str, decisions: list[dict[str, Any]], apply_mode: str) -> dict[str, Any]:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError("cleanup_session_not_found")

    by_id = {item["item_id"]: item for item in session["items"]}
    accepted = 0
    rejected = 0
    edited = 0
    for decision in decisions:
        item_id = str(decision.get("item_id") or "")
        if item_id not in by_id:
            continue
        item = by_id[item_id]
        state = str(decision.get("decision") or "pending")
        item["decision"] = state
        if state == "reject":
            rejected += 1
            continue

        final_action = decision.get("final_action") or item.get("suggested_action")
        final_target = decision.get("final_target_tag")
        if final_target is None:
            final_target = item.get("suggested_target_tag")
        item["final_action"] = str(final_action or "keep")
        item["final_target_tag"] = str(final_target or "")
        item["final_category"] = decision.get("final_category")
        accepted += 1
        if state == "edit_accept":
            edited += 1

    merge_count = 0
    rename_count = 0
    deprecate_count = 0
    categorize_count = 0
    mapping: list[dict[str, Any]] = []
    for item in session["items"]:
        if item.get("decision") not in {"accept", "edit_accept"}:
            continue
        action = str(item.get("final_action") or item.get("suggested_action") or "keep")
        if action == "merge":
            merge_count += 1
        elif action == "rename":
            rename_count += 1
        elif action == "deprecate":
            deprecate_count += 1
        elif action == "categorize":
            categorize_count += 1
        mapping.append(
            {
                "source_tag": item.get("source_tag"),
                "final_action": action,
                "final_target_tag": item.get("final_target_tag") or item.get("suggested_target_tag") or "",
                "final_category": item.get("final_category") or item.get("suggested_category"),
            }
        )

    status = "dry_run_ready" if apply_mode == "dry_run" else "applied"
    session["status"] = status
    return {
        "success": True,
        "session_id": session_id,
        "status": status,
        "summary": {
            "accepted": accepted,
            "rejected": rejected,
            "edited": edited,
            "merge_count": merge_count,
            "rename_count": rename_count,
            "deprecate_count": deprecate_count,
            "categorize_count": categorize_count,
        },
        "mapping": mapping,
    }


def export_cleanup(*, session_id: str, export_type: str) -> list[dict[str, Any]]:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError("cleanup_session_not_found")
    if export_type == "suggestions":
        return list(session["items"])
    rows: list[dict[str, Any]] = []
    for item in session["items"]:
        if item.get("decision") not in {"accept", "edit_accept"}:
            continue
        rows.append(
            {
                "source_tag": item.get("source_tag"),
                "final_action": item.get("final_action") or item.get("suggested_action") or "keep",
                "final_target_tag": item.get("final_target_tag") or item.get("suggested_target_tag") or "",
                "final_category": item.get("final_category") or item.get("suggested_category"),
            }
        )
    return rows


def get_cleanup_session(*, session_id: str) -> dict[str, Any]:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError("cleanup_session_not_found")

    items = list(session.get("items") or [])
    accepted = 0
    rejected = 0
    pending = 0
    for item in items:
        decision = str(item.get("decision") or "pending")
        if decision in {"accept", "edit_accept"}:
            accepted += 1
        elif decision == "reject":
            rejected += 1
        else:
            pending += 1

    return {
        "session_id": str(session.get("session_id") or session_id),
        "status": str(session.get("status") or "preview_ready"),
        "summary": {
            "total_input_tags": len(items),
            "total_suggestions": len(items),
            "high_confidence_count": len([x for x in items if float(x.get("confidence") or 0.0) >= 0.85]),
            "accepted": accepted,
            "rejected": rejected,
            "pending": pending,
        },
        "items": items,
    }
