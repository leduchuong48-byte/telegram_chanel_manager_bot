"""In-memory one-shot tag cleanup service."""

from __future__ import annotations

import time
import sqlite3
import json
from pathlib import Path
import uuid
from typing import Any

from app.services import tag_cleanup_rules

_SESSIONS: dict[str, dict[str, Any]] = {}
_RULES_CACHE: dict[str, str] | None = None
_SESSION_SEQ = 0
_DEFAULT_CHAT_ID = -1003000143874


def _ensure_cleanup_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_cleanup_sessions (
          session_id TEXT PRIMARY KEY,
          source_type TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          summary_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_cleanup_items (
          session_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          source_tag TEXT NOT NULL,
          normalized_tag TEXT,
          suggested_action TEXT NOT NULL,
          suggested_target_tag TEXT,
          suggested_category TEXT,
          category_l1 TEXT,
          category_l2 TEXT,
          rule_source TEXT,
          reason TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 0,
          samples_json TEXT NOT NULL DEFAULT '[]',
          decision TEXT NOT NULL DEFAULT 'pending',
          final_action TEXT,
          final_target_tag TEXT,
          final_category TEXT,
          PRIMARY KEY(session_id, item_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_definitions (
          chat_id INTEGER NOT NULL,
          tag TEXT NOT NULL,
          canonical_tag TEXT NOT NULL,
          category_l1 TEXT,
          category_l2 TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          source TEXT NOT NULL DEFAULT 'manual',
          confidence REAL,
          updated_at INTEGER NOT NULL,
          deprecated_reason TEXT,
          PRIMARY KEY(chat_id, tag)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_governance_audit (
          audit_id TEXT PRIMARY KEY,
          session_id TEXT,
          item_id TEXT,
          chat_id INTEGER,
          action TEXT NOT NULL,
          source_tag TEXT,
          target_tag TEXT,
          before_json TEXT NOT NULL DEFAULT '{}',
          after_json TEXT NOT NULL DEFAULT '{}',
          actor TEXT,
          created_at INTEGER NOT NULL
        )
        """
    )


def _persist_session_to_db(*, db_path: Path | None, session: dict[str, Any], summary: dict[str, Any]) -> None:
    if db_path is None:
        return
    conn = sqlite3.connect(db_path)
    try:
        _ensure_cleanup_schema(conn)
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO tag_cleanup_sessions(session_id, source_type, status, created_at, updated_at, summary_json)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
              source_type=excluded.source_type,
              status=excluded.status,
              updated_at=excluded.updated_at,
              summary_json=excluded.summary_json
            """,
            (
                str(session.get("session_id") or ""),
                str(session.get("source_type") or "manual_input"),
                str(session.get("status") or "preview_ready"),
                int(session.get("created_at") or now),
                now,
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        conn.execute("DELETE FROM tag_cleanup_items WHERE session_id=?", (str(session.get("session_id") or ""),))
        for item in list(session.get("items") or []):
            conn.execute(
                """
                INSERT INTO tag_cleanup_items(
                  session_id,item_id,source_tag,normalized_tag,suggested_action,suggested_target_tag,
                  suggested_category,category_l1,category_l2,rule_source,reason,confidence,samples_json,
                  decision,final_action,final_target_tag,final_category
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(session.get("session_id") or ""),
                    str(item.get("item_id") or ""),
                    str(item.get("source_tag") or ""),
                    str(item.get("normalized_tag") or "") or None,
                    str(item.get("suggested_action") or "keep"),
                    str(item.get("suggested_target_tag") or "") or None,
                    str(item.get("suggested_category") or "") or None,
                    str(item.get("category_l1") or "") or None,
                    str(item.get("category_l2") or "") or None,
                    str(item.get("rule_source") or "") or None,
                    str(item.get("reason") or ""),
                    float(item.get("confidence") or 0.0),
                    json.dumps(list(item.get("samples") or []), ensure_ascii=False),
                    str(item.get("decision") or "pending"),
                    str(item.get("final_action") or "") or None,
                    str(item.get("final_target_tag") or "") or None,
                    str(item.get("final_category") or "") or None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _make_session_id() -> str:
    return f"cleanup_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _next_session_seq() -> int:
    global _SESSION_SEQ
    _SESSION_SEQ += 1
    return _SESSION_SEQ


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


def _load_existing_rules() -> dict[str, str]:
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    root = Path(__file__).resolve().parents[2]
    rules = {}
    rules.update(tag_cleanup_rules.load_rename_rules_from_file(root / 'data' / 'tag_aliases' / 'global.txt'))
    rules.update(tag_cleanup_rules.load_rename_rules_from_file(root / 'data' / 'tag_aliases' / '-1003918648695.txt'))
    _RULES_CACHE = rules
    return rules


def _suggest_action(item: dict[str, Any]) -> dict[str, Any]:
    tag = str(item.get("tag") or "").strip().casefold()
    aliases = [str(x).strip().lstrip("#").casefold() for x in (item.get("aliases") or []) if str(x).strip()]
    target = ""
    action = "keep"
    reason = "保持不变"
    confidence = 0.65
    rule_source = "ai_suggested"

    rules = _load_existing_rules()
    rule_target = tag_cleanup_rules.resolve_target_tag(tag, rules)
    if rule_target:
        action = "rename" if rule_target.casefold() != tag else "keep"
        target = rule_target.casefold() if action == "rename" else ""
        reason = "命中已有rename规则"
        confidence = 0.99
        rule_source = "existing_rule"
    else:
        for alias in aliases:
            normalized_alias = tag_cleanup_rules.prefer_simplified_tag(alias).casefold()
            if normalized_alias and normalized_alias != tag:
                action = "rename"
                target = normalized_alias
                reason = "检测到同义标签，建议统一命名"
                confidence = 0.9
                break

        if action == "keep" and ("old" in tag or "废弃" in tag):
            action = "deprecate"
            reason = "疑似历史或废弃标签"
            confidence = 0.75

    source_tag = tag_cleanup_rules.prefer_simplified_tag(tag).casefold()
    category_l1 = tag_cleanup_rules.infer_category_l1(source_tag)
    category_l2 = tag_cleanup_rules.infer_category_l2(source_tag, category_l1)
    normalized_tag = source_tag

    return {
        "source_tag": source_tag,
        "normalized_tag": normalized_tag,
        "suggested_action": action,
        "suggested_target_tag": target,
        "suggested_category": category_l2,
        "category_l1": category_l1,
        "category_l2": category_l2,
        "rule_source": rule_source,
        "reason": reason,
        "confidence": confidence,
        "samples": [str(x) for x in (item.get("samples") or [])],
    }


def _collect_new_rename_rules(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = _load_existing_rules()
    new_rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if str(item.get("decision") or "") not in {"accept", "edit_accept"}:
            continue
        action = str(item.get("final_action") or item.get("suggested_action") or "keep")
        if action not in {"rename", "merge"}:
            continue

        source_tag = str(item.get("source_tag") or "").strip().casefold()
        target_tag = str(item.get("final_target_tag") or item.get("suggested_target_tag") or "").strip().casefold()
        if not source_tag or not target_tag or source_tag == target_tag:
            continue

        existing_target = existing.get(source_tag)
        if existing_target and existing_target.casefold() != target_tag:
            # conflict: existing rule disagrees, do not persist automatically
            continue

        key = (source_tag, target_tag)
        if key in seen:
            continue
        seen.add(key)
        new_rules.append(
            {
                "source_tag": source_tag,
                "target_tag": target_tag,
                "rule_source": str(item.get("rule_source") or "ai_suggested"),
                "category_l1": item.get("category_l1"),
                "category_l2": item.get("category_l2") or item.get("suggested_category"),
            }
        )
    return new_rules


def preview_cleanup(*, source_type: str, tag_items: list[dict[str, Any]], db_path: Path | None = None) -> dict[str, Any]:
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

    session = {
        "session_id": session_id,
        "source_type": source_type,
        "status": "preview_ready",
        "created_at": int(time.time()),
        "created_seq": _next_session_seq(),
        "items": items,
    }
    _SESSIONS[session_id] = session

    summary = {
        "total_input_tags": len(normalized_items),
        "total_suggestions": len(items),
        "high_confidence_count": high_conf,
    }
    _persist_session_to_db(db_path=db_path, session=session, summary=summary)

    return {
        "session_id": session_id,
        "status": "preview_ready",
        "summary": summary,
        "items": items,
    }


def apply_cleanup(*, session_id: str, decisions: list[dict[str, Any]], apply_mode: str, db_path: Path | None = None) -> dict[str, Any]:
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
    new_rename_rules = _collect_new_rename_rules(session["items"])
    session["new_rename_rules"] = new_rename_rules
    summary = {
        "total_input_tags": len(list(session.get("items") or [])),
        "total_suggestions": len(list(session.get("items") or [])),
        "high_confidence_count": len([x for x in list(session.get("items") or []) if float(x.get("confidence") or 0.0) >= 0.85]),
        "accepted": accepted,
        "rejected": rejected,
        "pending": max(0, len(list(session.get("items") or [])) - accepted - rejected),
    }
    _persist_session_to_db(db_path=db_path, session=session, summary=summary)
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
        "new_rename_rules": new_rename_rules,
    }


def export_cleanup(*, session_id: str, export_type: str) -> dict[str, Any]:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError("cleanup_session_not_found")
    if export_type == "suggestions":
        return {
            "content": list(session["items"]),
            "new_rename_rules": list(session.get("new_rename_rules") or []),
        }

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
    return {
        "content": rows,
        "new_rename_rules": list(session.get("new_rename_rules") or _collect_new_rename_rules(session["items"])),
    }


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


def list_cleanup_sessions(*, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 20), 200))
    sessions = sorted(
        _SESSIONS.values(),
        key=lambda row: (int(row.get("created_at") or 0), int(row.get("created_seq") or 0)),
        reverse=True,
    )[:safe_limit]

    result: list[dict[str, Any]] = []
    for session in sessions:
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

        result.append(
            {
                "session_id": str(session.get("session_id") or ""),
                "source_type": str(session.get("source_type") or "manual_input"),
                "status": str(session.get("status") or "preview_ready"),
                "created_at": int(session.get("created_at") or 0),
                "total_suggestions": len(items),
                "accepted": accepted,
                "rejected": rejected,
                "pending": pending,
            }
        )
    return result
