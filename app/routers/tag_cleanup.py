"""One-shot tag cleanup API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal
import sqlite3
import json
import time
import uuid
import threading

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.services import tag_cleanup as tag_cleanup_service
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/tag-cleanup", tags=["tag_cleanup"])
governance_router = APIRouter(prefix="/api/tag-governance", tags=["tag_governance"])

_config_manager: ConfigManager | None = None
_TAG_GOV_JOB_THREADS: dict[str, threading.Thread] = {}
_TAG_GOV_JOB_THREADS_LOCK = threading.Lock()


class TagCleanupTagInput(BaseModel):
    tag: str
    count: int = 0
    samples: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class TagCleanupOptions(BaseModel):
    max_items: int = 500
    include_category_suggestion: bool = True


class TagCleanupPreviewRequest(BaseModel):
    source_type: str
    tags: list[TagCleanupTagInput] = Field(default_factory=list)
    options: TagCleanupOptions = Field(default_factory=TagCleanupOptions)


class TagCleanupSummary(BaseModel):
    total_input_tags: int
    total_suggestions: int
    high_confidence_count: int


class TagCleanupItem(BaseModel):
    item_id: str
    source_tag: str
    normalized_tag: str | None = None
    suggested_action: str
    suggested_target_tag: str | None = None
    suggested_category: str | None = None
    category_l1: str | None = None
    category_l2: str | None = None
    rule_source: str | None = None
    reason: str
    confidence: float
    samples: list[str] = Field(default_factory=list)
    decision: str = "pending"
    final_action: str | None = None
    final_target_tag: str | None = None
    final_category: str | None = None


class TagCleanupPreviewResponse(BaseModel):
    session_id: str
    status: str
    summary: TagCleanupSummary
    items: list[TagCleanupItem] = Field(default_factory=list)


class TagCleanupDecision(BaseModel):
    item_id: str
    decision: Literal["accept", "reject", "edit_accept", "pending"]
    final_action: str | None = None
    final_target_tag: str | None = None
    final_category: str | None = None


class TagCleanupApplyRequest(BaseModel):
    session_id: str
    decisions: list[TagCleanupDecision] = Field(default_factory=list)
    apply_mode: Literal["write", "dry_run"] = "dry_run"
    confirm_token: str | None = None


class TagCleanupApplySummary(BaseModel):
    accepted: int
    rejected: int
    edited: int
    merge_count: int
    rename_count: int
    deprecate_count: int
    categorize_count: int


class TagCleanupMappingItem(BaseModel):
    source_tag: str
    final_action: str
    final_target_tag: str = ""
    final_category: str | None = None


class RenameRuleItem(BaseModel):
    source_tag: str
    target_tag: str
    rule_source: str | None = None
    category_l1: str | None = None
    category_l2: str | None = None


class TagCleanupApplyResponse(BaseModel):
    success: bool
    session_id: str
    status: str
    summary: TagCleanupApplySummary
    mapping: list[TagCleanupMappingItem] = Field(default_factory=list)
    new_rename_rules: list[RenameRuleItem] = Field(default_factory=list)


class TagCleanupExportRequest(BaseModel):
    session_id: str
    format: Literal["json", "csv"] = "json"
    export_type: Literal["suggestions", "final_mapping"] = "final_mapping"


class TagCleanupExportResponse(BaseModel):
    success: bool
    session_id: str
    format: str
    export_type: str
    filename: str
    content: list[dict]
    new_rename_rules: list[RenameRuleItem] = Field(default_factory=list)


class TagCleanupSessionSummary(BaseModel):
    total_input_tags: int
    total_suggestions: int
    high_confidence_count: int
    accepted: int
    rejected: int
    pending: int


class TagCleanupSessionResponse(BaseModel):
    session_id: str
    status: str
    summary: TagCleanupSessionSummary
    items: list[TagCleanupItem] = Field(default_factory=list)


class TagCleanupSessionListItem(BaseModel):
    session_id: str
    source_type: str
    status: str
    created_at: int
    total_suggestions: int
    accepted: int
    rejected: int
    pending: int


class TagCleanupSessionListResponse(BaseModel):
    count: int
    items: list[TagCleanupSessionListItem] = Field(default_factory=list)


class TagGovernanceResolveResponse(BaseModel):
    chat_id: int
    input_tag: str
    normalized_tag: str
    canonical_tag: str
    status: str
    category_l1: str | None = None
    category_l2: str | None = None
    alias_chain: list[str] = Field(default_factory=list)


class TagGovernanceTagItem(BaseModel):
    tag: str
    canonical_tag: str
    status: str
    category_l1: str | None = None
    category_l2: str | None = None
    source: str | None = None
    confidence: float | None = None
    updated_at: int


class TagGovernanceTagsResponse(BaseModel):
    count: int
    items: list[TagGovernanceTagItem] = Field(default_factory=list)


class TagGovernanceAuditItem(BaseModel):
    audit_id: str
    session_id: str | None = None
    item_id: str | None = None
    chat_id: int
    action: str
    source_tag: str | None = None
    target_tag: str | None = None
    actor: str | None = None
    created_at: int


class TagGovernanceAuditResponse(BaseModel):
    count: int
    items: list[TagGovernanceAuditItem] = Field(default_factory=list)


class TagGovernanceJobCreateRequest(BaseModel):
    session_id: str
    chat_id: int
    job_type: Literal["backfill_entity_tags", "rebuild_tag_library"] = "backfill_entity_tags"
    payload: dict[str, Any] = Field(default_factory=dict)


class TagGovernanceJobResponse(BaseModel):
    success: bool
    job_id: str
    session_id: str | None = None
    chat_id: int
    job_type: str
    status: str
    total_items: int = 0
    processed_items: int = 0
    failed_items: int = 0
    retry_count: int = 0
    replacements_checked: int = 0
    error: str = ""
    created_at: int
    updated_at: int
    finished_at: int = 0


class TagGovernanceJobFailureItem(BaseModel):
    message_id: int
    error: str
    error_code: str = ""
    stage: str = ""
    failed_at: int = 0


class TagGovernanceJobFailuresResponse(BaseModel):
    job_id: str
    count: int
    items: list[TagGovernanceJobFailureItem] = Field(default_factory=list)


class TagGovernanceJobEventItem(BaseModel):
    event_id: int
    event_type: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: int


class TagGovernanceJobEventsResponse(BaseModel):
    job_id: str
    count: int
    items: list[TagGovernanceJobEventItem] = Field(default_factory=list)


class TagGovernanceStatItem(BaseModel):
    key: str
    count: int


class TagGovernanceWindowBucket(BaseModel):
    window_key: str
    seconds: int
    event_count: int
    failure_count: int


class TagGovernanceTopFailedMessage(BaseModel):
    message_id: int
    count: int


class TagGovernanceStageErrorItem(BaseModel):
    stage: str
    error_code: str
    count: int


class TagGovernanceJobStatsResponse(BaseModel):
    job_id: str
    window_seconds: int = 0
    event_count: int
    failure_count: int
    event_types: list[TagGovernanceStatItem] = Field(default_factory=list)
    error_codes: list[TagGovernanceStatItem] = Field(default_factory=list)
    stage_counts: list[TagGovernanceStatItem] = Field(default_factory=list)
    stage_error_matrix: list[TagGovernanceStageErrorItem] = Field(default_factory=list)
    window_buckets: list[TagGovernanceWindowBucket] = Field(default_factory=list)
    top_failed_messages: list[TagGovernanceTopFailedMessage] = Field(default_factory=list)


class TagGovernanceJobOverviewResponse(BaseModel):
    job: TagGovernanceJobResponse
    stats: TagGovernanceJobStatsResponse
    events: TagGovernanceJobEventsResponse
    failures: TagGovernanceJobFailuresResponse


def set_config_manager(manager: ConfigManager) -> None:
    global _config_manager
    _config_manager = manager


def _ensure_config_manager() -> None:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )


def _project_root() -> Path:
    _ensure_config_manager()
    config_path = Path(getattr(_config_manager, "config_path", "config.json"))
    return config_path.resolve().parent


def _resolve_db_path() -> Path:
    _ensure_config_manager()
    config = _config_manager.get_config() if _config_manager is not None else {}
    db_cfg = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(db_cfg, dict):
        db_cfg = {}
    raw = str(db_cfg.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _record_workflow_event(*, event_type: str, result: str = "") -> None:
    try:
        db = Database(_resolve_db_path())
    except Exception:  # noqa: BLE001
        return
    try:
        db.record_ai_workflow_event(event_type=event_type, result=result)
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.close()


def _normalize_alias_tag(raw: str) -> str:
    return str(raw or "").strip().lstrip("#").casefold()


def _load_existing_alias_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        left, right = line.split("=", 1)
        src = _normalize_alias_tag(left)
        tgt = _normalize_alias_tag(right)
        if not src or not tgt or src == tgt:
            continue
        mapping[src] = tgt
    return mapping


def _persist_new_rename_rules(new_rules: list[dict[str, Any]]) -> None:
    alias_path = _project_root() / "data" / "tag_aliases" / "global.txt"
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_alias_mapping(alias_path)

    lines_to_append: list[str] = []
    for rule in new_rules:
        src = _normalize_alias_tag(str(rule.get("source_tag") or ""))
        tgt = _normalize_alias_tag(str(rule.get("target_tag") or ""))
        if not src or not tgt or src == tgt:
            continue
        current = existing.get(src)
        if current and current != tgt:
            continue
        if current == tgt:
            continue
        existing[src] = tgt
        lines_to_append.append(f"#{src}=#{tgt}")

    if not lines_to_append:
        return

    content = alias_path.read_text(encoding="utf-8", errors="ignore") if alias_path.exists() else ""
    prefix = "" if (not content or content.endswith("\n")) else "\n"
    alias_path.write_text(
        content + prefix + "\n".join(lines_to_append) + "\n",
        encoding="utf-8",
    )


def _resolve_apply_chat_id(db_path: Path) -> int:
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM tag_library ORDER BY chat_id ASC LIMIT 1")
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        cur.execute("SELECT chat_id FROM managed_chats ORDER BY chat_id ASC LIMIT 1")
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return -1003000143874


def _apply_new_rename_rules_to_db(*, db_path: Path, new_rules: list[dict[str, Any]]) -> None:
    if not new_rules:
        return
    db = Database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        chat_id = _resolve_apply_chat_id(db_path)
        now = int(Path(db_path).stat().st_mtime) if Path(db_path).exists() else 0
        if now <= 0:
            import time as _time

            now = int(_time.time())

        _ensure_tag_governance_schema(conn)
        for rule in new_rules:
            source_tag = _normalize_alias_tag(str(rule.get("source_tag") or ""))
            target_tag = _normalize_alias_tag(str(rule.get("target_tag") or ""))
            if not source_tag or not target_tag or source_tag == target_tag:
                continue
            db.set_tag_alias(chat_id=chat_id, old_tag=source_tag, new_tag=target_tag)
            db.merge_tag_counts(chat_id=chat_id, old_tag=source_tag, new_tag=target_tag)
            conn.execute(
                """
                INSERT INTO tag_definitions(chat_id, tag, canonical_tag, category_l1, category_l2, status, source, confidence, updated_at, deprecated_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id, tag) DO UPDATE SET
                  canonical_tag=excluded.canonical_tag,
                  category_l1=excluded.category_l1,
                  category_l2=excluded.category_l2,
                  status=excluded.status,
                  source=excluded.source,
                  confidence=excluded.confidence,
                  updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    source_tag,
                    target_tag,
                    str(rule.get("category_l1") or "") or None,
                    str(rule.get("category_l2") or "") or None,
                    "alias",
                    str(rule.get("rule_source") or "cleanup_apply"),
                    1.0,
                    now,
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO tag_definitions(chat_id, tag, canonical_tag, category_l1, category_l2, status, source, confidence, updated_at, deprecated_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id, tag) DO UPDATE SET
                  canonical_tag=excluded.canonical_tag,
                  category_l1=COALESCE(tag_definitions.category_l1, excluded.category_l1),
                  category_l2=COALESCE(tag_definitions.category_l2, excluded.category_l2),
                  status='active',
                  source=COALESCE(tag_definitions.source, excluded.source),
                  confidence=COALESCE(tag_definitions.confidence, excluded.confidence),
                  updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    target_tag,
                    target_tag,
                    str(rule.get("category_l1") or "") or None,
                    str(rule.get("category_l2") or "") or None,
                    "active",
                    str(rule.get("rule_source") or "cleanup_apply"),
                    1.0,
                    now,
                    None,
                ),
            )
            conn.execute(
                """
                INSERT INTO tag_governance_audit(audit_id, session_id, item_id, chat_id, action, source_tag, target_tag, before_json, after_json, actor, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"audit_{now}_{source_tag[:16]}_{target_tag[:16]}",
                    None,
                    None,
                    chat_id,
                    "rename_apply",
                    source_tag,
                    target_tag,
                    "{}",
                    "{}",
                    "tag_cleanup",
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
        db.close()


def _ensure_tag_governance_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_governance_jobs (
          job_id TEXT PRIMARY KEY,
          session_id TEXT,
          chat_id INTEGER NOT NULL,
          job_type TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          cursor_json TEXT NOT NULL DEFAULT '{}',
          total_items INTEGER NOT NULL DEFAULT 0,
          processed_items INTEGER NOT NULL DEFAULT 0,
          failed_items INTEGER NOT NULL DEFAULT 0,
          retry_count INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          finished_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_governance_job_failures (
          detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL,
          chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          error TEXT NOT NULL,
          error_code TEXT NOT NULL,
          stage TEXT NOT NULL,
          failed_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_governance_job_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at INTEGER NOT NULL
        )
        """
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(tag_governance_jobs)").fetchall()
    }
    if "retry_count" not in columns:
        conn.execute("ALTER TABLE tag_governance_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")


def _fetch_governance_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT job_id, session_id, chat_id, job_type, status, payload_json, cursor_json, total_items, processed_items, failed_items, retry_count, error, created_at, updated_at, finished_at
        FROM tag_governance_jobs
        WHERE job_id=?
        """,
        (str(job_id),),
    ).fetchone()


def _append_governance_job_event(*, conn: sqlite3.Connection, job_id: str, event_type: str, detail: dict[str, Any] | None = None, created_at: int | None = None) -> None:
    ts = int(created_at or time.time())
    conn.execute(
        """
        INSERT INTO tag_governance_job_events(job_id, event_type, detail_json, created_at)
        VALUES(?,?,?,?)
        """,
        (str(job_id), str(event_type), json.dumps(detail or {}, ensure_ascii=False), ts),
    )


def _normalize_job_event_detail(*, event_type: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    src = detail if isinstance(detail, dict) else {}
    out = {
        "phase": str(src.get("phase") or "backfill"),
        "status": str(src.get("status") or ""),
        "processed": int(src.get("processed") or 0),
        "failed": int(src.get("failed") or 0),
        "total": int(src.get("total") or 0),
        "error": str(src.get("error") or ""),
        "batch_size": int(src.get("batch_size") or 0),
        "reason": str(src.get("reason") or ""),
        "retry_count_after": int(src.get("retry_count_after") or 0),
    }

    et = str(event_type or "")
    if et == "job_created":
        out["status"] = out["status"] or "queued"
    elif et == "job_started":
        out["status"] = out["status"] or "running"
    elif et == "job_progress":
        out["status"] = out["status"] or "running"
    elif et == "job_completed":
        out["status"] = out["status"] or "completed"
    elif et == "job_failed":
        out["status"] = out["status"] or "failed"
    elif et == "job_cancelled":
        out["status"] = out["status"] or "cancelled"
    elif et == "job_retried":
        out["status"] = out["status"] or "queued"
    return out


def _row_to_governance_job_response(row: sqlite3.Row) -> TagGovernanceJobResponse:
    try:
        cursor = json.loads(str(row["cursor_json"] or "{}"))
    except Exception:
        cursor = {}
    return TagGovernanceJobResponse(
        success=True,
        job_id=str(row["job_id"]),
        session_id=str(row["session_id"] or "") or None,
        chat_id=int(row["chat_id"]),
        job_type=str(row["job_type"]),
        status=str(row["status"]),
        total_items=int(row["total_items"] or 0),
        processed_items=int(row["processed_items"] or 0),
        failed_items=int(row["failed_items"] or 0),
        retry_count=int(row["retry_count"] or 0),
        replacements_checked=int(cursor.get("replacements_checked") or 0),
        error=str(row["error"] or ""),
        created_at=int(row["created_at"] or 0),
        updated_at=int(row["updated_at"] or 0),
        finished_at=int(row["finished_at"] or 0),
    )


def _run_backfill_job_once(*, conn: sqlite3.Connection, row: sqlite3.Row, batch_size: int) -> sqlite3.Row:
    now = int(time.time())
    chat_id = int(row["chat_id"])
    payload_json = str(row["payload_json"] or "{}")
    cursor_json = str(row["cursor_json"] or "{}")
    try:
        payload = json.loads(payload_json)
    except Exception:
        payload = {}
    try:
        cursor = json.loads(cursor_json)
    except Exception:
        cursor = {}
    offset = int(cursor.get("offset") or 0)
    prev_replacements_checked = int(cursor.get("replacements_checked") or 0)
    fail_streak = int(cursor.get("consecutive_failures") or 0)

    fail_ids: set[int] = set()
    for raw in list(payload.get("force_fail_message_ids") or []):
        try:
            fail_ids.add(int(raw))
        except Exception:
            continue

    replacement_map: dict[str, str] = {}
    for k, v in dict(payload.get("deprecated_replacements") or {}).items():
        old_tag = _normalize_alias_tag(str(k or ""))
        new_tag = _normalize_alias_tag(str(v or ""))
        if old_tag and new_tag and old_tag != new_tag:
            replacement_map[old_tag] = new_tag

    if not replacement_map:
        defs_exist = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tag_definitions'"
        ).fetchone() is not None
        if defs_exist:
            rows_rep = conn.execute(
                """
                SELECT tag, deprecated_reason
                FROM tag_definitions
                WHERE chat_id=? AND status='deprecated' AND deprecated_reason LIKE 'replacement:%'
                """,
                (chat_id,),
            ).fetchall()
            for rr in rows_rep:
                old_tag = _normalize_alias_tag(str(rr["tag"] or ""))
                reason = str(rr["deprecated_reason"] or "")
                new_tag = _normalize_alias_tag(reason.split(":", 1)[1] if ":" in reason else "")
                if old_tag and new_tag and old_tag != new_tag:
                    replacement_map[old_tag] = new_tag

    max_fail_ratio = None
    try:
        ratio_raw = payload.get("max_fail_ratio")
        if ratio_raw is not None:
            ratio = float(ratio_raw)
            if 0.0 <= ratio <= 1.0:
                max_fail_ratio = ratio
    except Exception:
        max_fail_ratio = None

    max_fail_streak = None
    try:
        streak_raw = payload.get("max_fail_streak")
        if streak_raw is not None:
            streak_v = int(streak_raw)
            if streak_v > 0:
                max_fail_streak = streak_v
    except Exception:
        max_fail_streak = None

    media_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_messages'"
    ).fetchone() is not None
    rows: list[Any] = []
    if media_exists:
        total = int(
            conn.execute(
                "SELECT COUNT(*) FROM media_messages WHERE chat_id=?",
                (chat_id,),
            ).fetchone()[0]
        )
        rows = conn.execute(
            """
            SELECT message_id FROM media_messages
            WHERE chat_id=?
            ORDER BY message_id ASC
            LIMIT ? OFFSET ?
            """,
            (chat_id, int(batch_size), offset),
        ).fetchall()
        consumed = len(rows)
    else:
        total = 0
        consumed = 0

    batch_success = 0
    batch_failed = 0
    consumed_actual = 0
    last_error = ""
    failed_ids = list(cursor.get("failed_message_ids") or [])
    failure_details = list(cursor.get("failure_details") or [])
    replacements_checked = prev_replacements_checked

    fail_streak_exceeded = False
    for r in rows:
        consumed_actual += 1
        msg_id = int(r["message_id"])
        try:
            if msg_id in fail_ids:
                raise RuntimeError(f"forced_fail_message:{msg_id}")
            if replacement_map:
                replacements_checked += 1
            batch_success += 1
            fail_streak = 0
        except Exception as exc:
            batch_failed += 1
            fail_streak += 1
            if not last_error:
                last_error = str(exc)
            failed_ids.append(msg_id)
            err_text = str(exc)
            err_code = err_text.split(":", 1)[0] if ":" in err_text else "run_error"
            failure_details.append(
                {
                    "message_id": msg_id,
                    "error": err_text,
                    "error_code": err_code,
                    "stage": "scan_batch",
                    "failed_at": now,
                }
            )
            conn.execute(
                """
                INSERT INTO tag_governance_job_failures(job_id, chat_id, message_id, error, error_code, stage, failed_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (str(row["job_id"]), chat_id, msg_id, err_text, err_code, "scan_batch", now),
            )
            if max_fail_streak is not None and fail_streak >= max_fail_streak:
                fail_streak_exceeded = True
                break

    if len(failed_ids) > 50:
        failed_ids = failed_ids[-50:]
    if len(failure_details) > 200:
        failure_details = failure_details[-200:]

    new_processed = min(int(row["processed_items"] or 0) + batch_success, total)
    new_failed_total = int(row["failed_items"] or 0) + batch_failed
    new_offset = offset + consumed_actual
    fail_ratio_exceeded = False
    if max_fail_ratio is not None and consumed_actual > 0:
        fail_ratio_exceeded = (batch_failed / float(consumed_actual)) > max_fail_ratio
    done = new_offset >= total or fail_ratio_exceeded or fail_streak_exceeded
    if fail_ratio_exceeded or fail_streak_exceeded:
        new_status = "failed"
    else:
        new_status = "completed" if done else "running"
    existing_error = str(row["error"] or "")
    ratio_error = ""
    if fail_ratio_exceeded:
        ratio_error = f"fail_ratio_exceeded:{batch_failed}/{consumed_actual}> {max_fail_ratio}"
    streak_error = ""
    if fail_streak_exceeded:
        streak_error = f"fail_streak_exceeded:{fail_streak}>={max_fail_streak}"
    new_error = ratio_error or streak_error or existing_error or last_error

    conn.execute(
        """
        UPDATE tag_governance_jobs
        SET status=?, payload_json=?, cursor_json=?, total_items=?, processed_items=?, failed_items=?, error=?, updated_at=?, finished_at=?
        WHERE job_id=?
        """,
        (
            new_status,
            payload_json,
            json.dumps(
                {
                    "offset": new_offset,
                    "failed_message_ids": failed_ids,
                    "failure_details": failure_details,
                    "replacements_checked": replacements_checked,
                    "consecutive_failures": fail_streak,
                },
                ensure_ascii=False,
            ),
            total,
            new_processed,
            new_failed_total,
            new_error,
            now,
            now if done else 0,
            str(row["job_id"]),
        ),
    )
    event_type = "job_progress"
    if new_status == "failed":
        event_type = "job_failed"
    elif new_status == "completed":
        event_type = "job_completed"
    _append_governance_job_event(
        conn=conn,
        job_id=str(row["job_id"]),
        event_type=event_type,
        detail={
            "processed": new_processed,
            "failed": new_failed_total,
            "total": total,
            "error": new_error,
        },
        created_at=now,
    )
    conn.commit()
    updated = _fetch_governance_job(conn, str(row["job_id"]))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tag_governance_job_run_failed")
    return updated


def _run_job_once_by_id(*, db_path: Path, job_id: str, batch_size: int) -> TagGovernanceJobResponse:
    conn = sqlite3.connect(db_path)
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
        current_status = str(row["status"])
        if current_status in {"cancelled", "completed", "failed"}:
            return _row_to_governance_job_response(row)
        updated = _run_backfill_job_once(conn=conn, row=row, batch_size=batch_size)
    finally:
        conn.close()
    return _row_to_governance_job_response(updated)


def _mark_job_failed_best_effort(*, db_path: Path, job_id: str, reason: str) -> None:
    try:
        conn = sqlite3.connect(db_path)
        try:
            _ensure_tag_governance_schema(conn)
            now = int(time.time())
            conn.execute(
                "UPDATE tag_governance_jobs SET status='failed', error=?, updated_at=?, finished_at=? WHERE job_id=?",
                (str(reason)[:500], now, now, str(job_id)),
            )
            _append_governance_job_event(
                conn=conn,
                job_id=job_id,
                event_type="job_failed",
                detail={"error": str(reason)[:500], "phase": "worker"},
                created_at=now,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return


def _background_run_tag_governance_job(*, db_path: Path, job_id: str, batch_size: int) -> None:
    try:
        for _ in range(100000):
            try:
                result = _run_job_once_by_id(db_path=db_path, job_id=job_id, batch_size=batch_size)
            except Exception as exc:
                _mark_job_failed_best_effort(
                    db_path=db_path,
                    job_id=job_id,
                    reason=f"background_worker_exception:{exc}",
                )
                return
            if result.status in {"completed", "failed", "cancelled"}:
                return
            time.sleep(0.02)
    finally:
        with _TAG_GOV_JOB_THREADS_LOCK:
            _TAG_GOV_JOB_THREADS.pop(job_id, None)


@router.post("/preview", response_model=TagCleanupPreviewResponse)
async def preview_cleanup(
    payload: TagCleanupPreviewRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupPreviewResponse:
    _ensure_config_manager()
    if not payload.tags:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cleanup_empty_input")
    raw = tag_cleanup_service.preview_cleanup(
        source_type=str(payload.source_type or "manual_input"),
        tag_items=[item.model_dump() for item in payload.tags],
        db_path=_resolve_db_path(),
    )
    _record_workflow_event(event_type="preview_request")
    return TagCleanupPreviewResponse(**raw)


@router.post("/apply", response_model=TagCleanupApplyResponse)
async def apply_cleanup(
    payload: TagCleanupApplyRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupApplyResponse:
    _ensure_config_manager()
    if payload.apply_mode == "write" and str(payload.confirm_token or "").strip().upper() != "APPLY":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cleanup_write_confirm_required")
    try:
        raw = tag_cleanup_service.apply_cleanup(
            session_id=payload.session_id,
            decisions=[item.model_dump() for item in payload.decisions],
            apply_mode=payload.apply_mode,
            db_path=_resolve_db_path(),
        )
        if payload.apply_mode == "write":
            _persist_new_rename_rules(raw.get("new_rename_rules", []))
            _apply_new_rename_rules_to_db(db_path=_resolve_db_path(), new_rules=raw.get("new_rename_rules", []))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")

    for decision in payload.decisions:
        value = str(decision.decision)
        if value in {"accept", "edit_accept"}:
            _record_workflow_event(event_type="review_decision", result="approved")
        elif value == "reject":
            _record_workflow_event(event_type="review_decision", result="rejected")

    return TagCleanupApplyResponse(**raw)


@router.post("/export", response_model=TagCleanupExportResponse)
async def export_cleanup(
    payload: TagCleanupExportRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupExportResponse:
    _ensure_config_manager()
    try:
        exported = tag_cleanup_service.export_cleanup(
            session_id=payload.session_id,
            export_type=payload.export_type,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")

    filename = f"tag_cleanup_{payload.export_type}.{payload.format}"
    return TagCleanupExportResponse(
        success=True,
        session_id=payload.session_id,
        format=payload.format,
        export_type=payload.export_type,
        filename=filename,
        content=exported.get("content", []),
        new_rename_rules=exported.get("new_rename_rules", []),
    )


@router.get("/session/{session_id}", response_model=TagCleanupSessionResponse)
async def get_cleanup_session(
    session_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupSessionResponse:
    _ensure_config_manager()
    try:
        raw = tag_cleanup_service.get_cleanup_session(session_id=session_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")
    return TagCleanupSessionResponse(**raw)


@router.get("/sessions", response_model=TagCleanupSessionListResponse)
async def list_cleanup_sessions(
    limit: int = 20,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagCleanupSessionListResponse:
    _ensure_config_manager()
    rows = tag_cleanup_service.list_cleanup_sessions(limit=limit)
    return TagCleanupSessionListResponse(count=len(rows), items=[TagCleanupSessionListItem(**row) for row in rows])


@governance_router.get("/resolve", response_model=TagGovernanceResolveResponse)
async def resolve_tag_governance(
    chat_id: int,
    tag: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagGovernanceResolveResponse:
    _ensure_config_manager()
    normalized = _normalize_alias_tag(tag)
    chain: list[str] = [normalized] if normalized else []
    seen = set(chain)
    canonical = normalized

    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        for _i in range(10):
            if not canonical:
                break
            row = conn.execute(
                "SELECT new_tag FROM tag_aliases WHERE chat_id=? AND old_tag=?",
                (int(chat_id), canonical),
            ).fetchone()
            if row is None:
                break
            nxt = _normalize_alias_tag(str(row["new_tag"] or ""))
            if not nxt or nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            canonical = nxt

        source_def = conn.execute(
            "SELECT status, canonical_tag FROM tag_definitions WHERE chat_id=? AND tag=?",
            (int(chat_id), normalized),
        ).fetchone()
        canonical_def = conn.execute(
            "SELECT category_l1, category_l2 FROM tag_definitions WHERE chat_id=? AND tag=?",
            (int(chat_id), canonical),
        ).fetchone()
    finally:
        conn.close()

    status_value = str(source_def["status"] if source_def else ("active" if canonical else "unknown"))
    return TagGovernanceResolveResponse(
        chat_id=int(chat_id),
        input_tag=str(tag),
        normalized_tag=normalized,
        canonical_tag=canonical or normalized,
        status=status_value,
        category_l1=str(canonical_def["category_l1"] or "") if canonical_def else None,
        category_l2=str(canonical_def["category_l2"] or "") if canonical_def else None,
        alias_chain=chain,
    )


@governance_router.get("/tags", response_model=TagGovernanceTagsResponse)
async def list_tag_governance_tags(
    chat_id: int,
    limit: int = 100,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceTagsResponse:
    _ensure_config_manager()
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        rows = conn.execute(
            """
            SELECT tag, canonical_tag, status, category_l1, category_l2, source, confidence, updated_at
            FROM tag_definitions
            WHERE chat_id=?
            ORDER BY updated_at DESC, tag ASC
            LIMIT ?
            """,
            (int(chat_id), safe_limit),
        ).fetchall()
    finally:
        conn.close()
    items = [
        TagGovernanceTagItem(
            tag=str(row["tag"]),
            canonical_tag=str(row["canonical_tag"]),
            status=str(row["status"]),
            category_l1=str(row["category_l1"] or "") or None,
            category_l2=str(row["category_l2"] or "") or None,
            source=str(row["source"] or "") or None,
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            updated_at=int(row["updated_at"] or 0),
        )
        for row in rows
    ]
    return TagGovernanceTagsResponse(count=len(items), items=items)


@governance_router.get("/audit", response_model=TagGovernanceAuditResponse)
async def list_tag_governance_audit(
    chat_id: int,
    limit: int = 100,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceAuditResponse:
    _ensure_config_manager()
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        rows = conn.execute(
            """
            SELECT audit_id, session_id, item_id, chat_id, action, source_tag, target_tag, actor, created_at
            FROM tag_governance_audit
            WHERE chat_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(chat_id), safe_limit),
        ).fetchall()
    finally:
        conn.close()
    items = [
        TagGovernanceAuditItem(
            audit_id=str(row["audit_id"]),
            session_id=str(row["session_id"] or "") or None,
            item_id=str(row["item_id"] or "") or None,
            chat_id=int(row["chat_id"]),
            action=str(row["action"]),
            source_tag=str(row["source_tag"] or "") or None,
            target_tag=str(row["target_tag"] or "") or None,
            actor=str(row["actor"] or "") or None,
            created_at=int(row["created_at"] or 0),
        )
        for row in rows
    ]
    return TagGovernanceAuditResponse(count=len(items), items=items)


@router.post("/apply-jobs", response_model=TagGovernanceJobResponse)
async def create_tag_governance_job(
    payload: TagGovernanceJobCreateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_tag_governance_schema(conn)
        now = int(time.time())
        job_id = f"tgov_job_{now}_{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO tag_governance_jobs(
              job_id, session_id, chat_id, job_type, status, payload_json, cursor_json,
              total_items, processed_items, failed_items, retry_count, error, created_at, updated_at, finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                str(payload.session_id or "") or None,
                int(payload.chat_id),
                str(payload.job_type),
                "queued",
                json.dumps(payload.payload or {}, ensure_ascii=False),
                "{}",
                0,
                0,
                0,
                0,
                "",
                now,
                now,
                0,
            ),
        )
        _append_governance_job_event(
            conn=conn,
            job_id=job_id,
            event_type="job_created",
            detail={"status": "queued", "job_type": str(payload.job_type)},
            created_at=now,
        )
        conn.commit()
        row = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tag_governance_job_create_failed")
    return _row_to_governance_job_response(row)


@router.get("/jobs/{job_id}", response_model=TagGovernanceJobResponse)
async def get_tag_governance_job(
    job_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
    return _row_to_governance_job_response(row)


@router.get("/jobs/{job_id}/failures", response_model=TagGovernanceJobFailuresResponse)
async def get_tag_governance_job_failures(
    job_id: str,
    limit: int = 100,
    offset: int = 0,
    error_code: str = "",
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobFailuresResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")

    safe_error_code = str(error_code or "").strip()
    items_all: list[TagGovernanceJobFailureItem] = []

    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        where = "WHERE job_id=?"
        params: list[Any] = [str(job_id)]
        if safe_error_code:
            where += " AND error_code=?"
            params.append(safe_error_code)
        count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM tag_governance_job_failures {where}",
            tuple(params),
        ).fetchone()
        total_count = int(count_row["c"] if count_row else 0)

        rows = conn.execute(
            f"""
            SELECT message_id, error, error_code, stage, failed_at
            FROM tag_governance_job_failures
            {where}
            ORDER BY failed_at DESC, message_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))]),
        ).fetchall()
    finally:
        conn.close()

    if rows:
        for d in rows:
            items_all.append(
                TagGovernanceJobFailureItem(
                    message_id=int(d["message_id"]),
                    error=str(d["error"] or "failed"),
                    error_code=str(d["error_code"] or "run_error"),
                    stage=str(d["stage"] or "scan_batch"),
                    failed_at=int(d["failed_at"] or 0),
                )
            )
        return TagGovernanceJobFailuresResponse(job_id=str(job_id), count=total_count, items=items_all)

    try:
        cursor = json.loads(str(row["cursor_json"] or "{}"))
    except Exception:
        cursor = {}
    details_raw = list(cursor.get("failure_details") or [])
    failed_ids_raw = list(cursor.get("failed_message_ids") or [])
    error_text = str(row["error"] or "")

    for d in details_raw:
        try:
            msg_id = int(d.get("message_id"))
        except Exception:
            continue
        it = TagGovernanceJobFailureItem(
            message_id=msg_id,
            error=str(d.get("error") or error_text or "failed"),
            error_code=str(d.get("error_code") or "run_error"),
            stage=str(d.get("stage") or "scan_batch"),
            failed_at=int(d.get("failed_at") or 0),
        )
        if safe_error_code and it.error_code != safe_error_code:
            continue
        items_all.append(it)
    if not items_all:
        for raw in failed_ids_raw:
            try:
                msg_id = int(raw)
            except Exception:
                continue
            it = TagGovernanceJobFailureItem(
                message_id=msg_id,
                error=error_text or "failed",
                error_code="run_error",
                stage="scan_batch",
                failed_at=0,
            )
            if safe_error_code and it.error_code != safe_error_code:
                continue
            items_all.append(it)

    items_all.sort(key=lambda x: (int(x.failed_at or 0), int(x.message_id or 0)), reverse=True)
    safe_limit = max(1, min(int(limit or 100), 500))
    safe_offset = max(0, int(offset or 0))
    page_items = items_all[safe_offset : safe_offset + safe_limit]

    return TagGovernanceJobFailuresResponse(job_id=str(job_id), count=len(items_all), items=page_items)


@router.get("/jobs/{job_id}/events", response_model=TagGovernanceJobEventsResponse)
async def get_tag_governance_job_events(
    job_id: str,
    limit: int = 100,
    offset: int = 0,
    event_type: str = "",
    since_event_id: int = 0,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobEventsResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        exists = _fetch_governance_job(conn, job_id)
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
        safe_event_type = str(event_type or "").strip()
        safe_since = max(0, int(since_event_id or 0))
        where = "WHERE job_id=?"
        params: list[Any] = [str(job_id)]
        if safe_event_type:
            where += " AND event_type=?"
            params.append(safe_event_type)
        if safe_since > 0:
            where += " AND event_id>?"
            params.append(safe_since)

        count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM tag_governance_job_events {where}",
            tuple(params),
        ).fetchone()
        total_count = int(count_row["c"] if count_row else 0)
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, detail_json, created_at
            FROM tag_governance_job_events
            {where}
            ORDER BY event_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))]),
        ).fetchall()
    finally:
        conn.close()

    items: list[TagGovernanceJobEventItem] = []
    for row in rows:
        try:
            detail = json.loads(str(row["detail_json"] or "{}"))
        except Exception:
            detail = {}
        et = str(row["event_type"] or "")
        items.append(
            TagGovernanceJobEventItem(
                event_id=int(row["event_id"] or 0),
                event_type=et,
                detail=_normalize_job_event_detail(event_type=et, detail=detail),
                created_at=int(row["created_at"] or 0),
            )
        )

    return TagGovernanceJobEventsResponse(job_id=str(job_id), count=total_count, items=items)


@router.get("/jobs/{job_id}/stats", response_model=TagGovernanceJobStatsResponse)
async def get_tag_governance_job_stats(
    job_id: str,
    window_seconds: int = 0,
    include_windows: bool = False,
    top_limit: int = 5,
    event_type: str = "",
    error_code: str = "",
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobStatsResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        _ensure_tag_governance_schema(conn)
        exists = _fetch_governance_job(conn, job_id)
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")

        safe_window = max(0, int(window_seconds or 0))
        now = int(time.time())
        min_ts = now - safe_window if safe_window > 0 else 0

        safe_event_type = str(event_type or "").strip()
        safe_error_code = str(error_code or "").strip()

        event_where = "job_id=?"
        event_params: list[Any] = [str(job_id)]
        failure_where = "job_id=?"
        failure_params: list[Any] = [str(job_id)]

        if safe_window > 0:
            event_where += " AND created_at>=?"
            event_params.append(min_ts)
            failure_where += " AND failed_at>=?"
            failure_params.append(min_ts)
        if safe_event_type:
            event_where += " AND event_type=?"
            event_params.append(safe_event_type)
        if safe_error_code:
            failure_where += " AND error_code=?"
            failure_params.append(safe_error_code)

        event_count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM tag_governance_job_events WHERE {event_where}",
            tuple(event_params),
        ).fetchone()
        failure_count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM tag_governance_job_failures WHERE {failure_where}",
            tuple(failure_params),
        ).fetchone()

        event_rows = conn.execute(
            f"""
            SELECT event_type, COUNT(*) AS c
            FROM tag_governance_job_events
            WHERE {event_where}
            GROUP BY event_type
            ORDER BY c DESC, event_type ASC
            """,
            tuple(event_params),
        ).fetchall()
        err_rows = conn.execute(
            f"""
            SELECT error_code, COUNT(*) AS c
            FROM tag_governance_job_failures
            WHERE {failure_where}
            GROUP BY error_code
            ORDER BY c DESC, error_code ASC
            """,
            tuple(failure_params),
        ).fetchall()
        stage_rows = conn.execute(
            f"""
            SELECT stage, COUNT(*) AS c
            FROM tag_governance_job_failures
            WHERE {failure_where}
            GROUP BY stage
            ORDER BY c DESC, stage ASC
            """,
            tuple(failure_params),
        ).fetchall()
        stage_error_rows = conn.execute(
            f"""
            SELECT stage, error_code, COUNT(*) AS c
            FROM tag_governance_job_failures
            WHERE {failure_where}
            GROUP BY stage, error_code
            ORDER BY c DESC, stage ASC, error_code ASC
            """,
            tuple(failure_params),
        ).fetchall()

        safe_top = max(1, min(int(top_limit or 5), 50))
        top_rows = conn.execute(
            """
            SELECT message_id, COUNT(*) AS c
            FROM tag_governance_job_failures
            WHERE """ + failure_where + """
            GROUP BY message_id
            ORDER BY c DESC, message_id DESC
            LIMIT ?
            """,
            tuple(failure_params + [safe_top]),
        ).fetchall()

        window_rows: list[TagGovernanceWindowBucket] = []
        if bool(include_windows):
            now = int(time.time())
            windows = [("1h", 3600), ("24h", 86400), ("7d", 604800)]
            for key, seconds in windows:
                min_ts_w = now - int(seconds)
                e_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM tag_governance_job_events WHERE job_id=? AND created_at>=?",
                    (str(job_id), min_ts_w),
                ).fetchone()
                f_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM tag_governance_job_failures WHERE job_id=? AND failed_at>=?",
                    (str(job_id), min_ts_w),
                ).fetchone()
                window_rows.append(
                    TagGovernanceWindowBucket(
                        window_key=key,
                        seconds=int(seconds),
                        event_count=int(e_row["c"] if e_row else 0),
                        failure_count=int(f_row["c"] if f_row else 0),
                    )
                )
    finally:
        conn.close()

    event_types = [
        TagGovernanceStatItem(key=str(r["event_type"] or ""), count=int(r["c"] or 0))
        for r in event_rows
    ]
    error_codes = [
        TagGovernanceStatItem(key=str(r["error_code"] or ""), count=int(r["c"] or 0))
        for r in err_rows
    ]
    stage_counts = [
        TagGovernanceStatItem(key=str(r["stage"] or ""), count=int(r["c"] or 0))
        for r in stage_rows
    ]
    top_failed_messages = [
        TagGovernanceTopFailedMessage(message_id=int(r["message_id"] or 0), count=int(r["c"] or 0))
        for r in top_rows
    ]
    stage_error_matrix = [
        TagGovernanceStageErrorItem(
            stage=str(r["stage"] or ""),
            error_code=str(r["error_code"] or ""),
            count=int(r["c"] or 0),
        )
        for r in stage_error_rows
    ]

    return TagGovernanceJobStatsResponse(
        job_id=str(job_id),
        window_seconds=safe_window,
        event_count=int(event_count_row["c"] if event_count_row else 0),
        failure_count=int(failure_count_row["c"] if failure_count_row else 0),
        event_types=event_types,
        error_codes=error_codes,
        stage_counts=stage_counts,
        stage_error_matrix=stage_error_matrix,
        window_buckets=window_rows,
        top_failed_messages=top_failed_messages,
    )


_COMPACT_OVERVIEW_STATS_TOP_LIMIT = 3
_COMPACT_OVERVIEW_EVENTS_LIMIT = 10
_COMPACT_OVERVIEW_FAILURES_LIMIT = 10


@router.get("/jobs/{job_id}/overview", response_model=TagGovernanceJobOverviewResponse)
async def get_tag_governance_job_overview(
    job_id: str,
    stats_window_seconds: int = 0,
    stats_include_windows: bool = False,
    stats_top_limit: int = 5,
    stats_event_type: str = "",
    stats_error_code: str = "",
    events_limit: int = 20,
    events_offset: int = 0,
    events_event_type: str = "",
    events_since_event_id: int = 0,
    failures_limit: int = 20,
    failures_offset: int = 0,
    failures_error_code: str = "",
    compact: bool = False,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobOverviewResponse:
    _ensure_config_manager()
    job = await get_tag_governance_job(job_id=job_id, _="admin")

    effective_include_windows = bool(stats_include_windows)
    effective_top_limit = int(stats_top_limit)
    effective_events_limit = int(events_limit)
    effective_failures_limit = int(failures_limit)

    if compact:
        effective_include_windows = False
        effective_top_limit = min(max(effective_top_limit, 1), _COMPACT_OVERVIEW_STATS_TOP_LIMIT)
        effective_events_limit = min(max(effective_events_limit, 1), _COMPACT_OVERVIEW_EVENTS_LIMIT)
        effective_failures_limit = min(max(effective_failures_limit, 1), _COMPACT_OVERVIEW_FAILURES_LIMIT)

    stats = await get_tag_governance_job_stats(
        job_id=job_id,
        window_seconds=stats_window_seconds,
        include_windows=effective_include_windows,
        top_limit=effective_top_limit,
        event_type=stats_event_type,
        error_code=stats_error_code,
        _="admin",
    )
    events = await get_tag_governance_job_events(
        job_id=job_id,
        limit=effective_events_limit,
        offset=events_offset,
        event_type=events_event_type,
        since_event_id=events_since_event_id,
        _="admin",
    )
    failures = await get_tag_governance_job_failures(
        job_id=job_id,
        limit=effective_failures_limit,
        offset=failures_offset,
        error_code=failures_error_code,
        _="admin",
    )
    return TagGovernanceJobOverviewResponse(
        job=job,
        stats=stats,
        events=events,
        failures=failures,
    )


@router.post("/jobs/{job_id}/cancel", response_model=TagGovernanceJobResponse)
async def cancel_tag_governance_job(
    job_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
        status_value = str(row["status"])
        if status_value in {"completed", "failed"}:
            return _row_to_governance_job_response(row)
        now = int(time.time())
        conn.execute(
            "UPDATE tag_governance_jobs SET status='cancelled', updated_at=?, finished_at=? WHERE job_id=?",
            (now, now, job_id),
        )
        _append_governance_job_event(
            conn=conn,
            job_id=job_id,
            event_type="job_cancelled",
            detail={"reason": "manual_cancel"},
            created_at=now,
        )
        conn.commit()
        updated = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tag_governance_job_cancel_failed")
    return _row_to_governance_job_response(updated)


@router.post("/jobs/{job_id}/retry", response_model=TagGovernanceJobResponse)
async def retry_tag_governance_job(
    job_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    conn = sqlite3.connect(_resolve_db_path())
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
        now = int(time.time())
        conn.execute(
            """
            UPDATE tag_governance_jobs
            SET status='queued', updated_at=?, finished_at=0, error='', retry_count=COALESCE(retry_count,0)+1
            WHERE job_id=?
            """,
            (now, job_id),
        )
        _append_governance_job_event(
            conn=conn,
            job_id=job_id,
            event_type="job_retried",
            detail={"retry_count_after": int(row["retry_count"] or 0) + 1},
            created_at=now,
        )
        conn.commit()
        updated = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tag_governance_job_retry_failed")
    return _row_to_governance_job_response(updated)


@router.post("/jobs/{job_id}/run-once", response_model=TagGovernanceJobResponse)
async def run_tag_governance_job_once(
    job_id: str,
    batch_size: int = 200,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    safe_batch = max(1, min(int(batch_size or 200), 2000))
    return _run_job_once_by_id(db_path=_resolve_db_path(), job_id=job_id, batch_size=safe_batch)


@router.post("/jobs/{job_id}/start", response_model=TagGovernanceJobResponse)
async def start_tag_governance_job(
    job_id: str,
    batch_size: int = 200,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> TagGovernanceJobResponse:
    _ensure_config_manager()
    safe_batch = max(1, min(int(batch_size or 200), 2000))
    db_path = _resolve_db_path()

    conn = sqlite3.connect(db_path)
    try:
        _ensure_tag_governance_schema(conn)
        row = _fetch_governance_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tag_governance_job_not_found")
        current_status = str(row["status"])
        if current_status in {"completed", "failed"}:
            return _row_to_governance_job_response(row)
        now = int(time.time())
        conn.execute(
            "UPDATE tag_governance_jobs SET status='running', updated_at=? WHERE job_id=?",
            (now, job_id),
        )
        _append_governance_job_event(
            conn=conn,
            job_id=job_id,
            event_type="job_started",
            detail={"batch_size": safe_batch},
            created_at=now,
        )
        conn.commit()
        updated = _fetch_governance_job(conn, job_id)
    finally:
        conn.close()

    with _TAG_GOV_JOB_THREADS_LOCK:
        existing = _TAG_GOV_JOB_THREADS.get(job_id)
        if existing is None or not existing.is_alive():
            th = threading.Thread(
                target=_background_run_tag_governance_job,
                kwargs={"db_path": db_path, "job_id": job_id, "batch_size": safe_batch},
                daemon=True,
            )
            _TAG_GOV_JOB_THREADS[job_id] = th
            th.start()

    if updated is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="tag_governance_job_start_failed")
    return _row_to_governance_job_response(updated)
