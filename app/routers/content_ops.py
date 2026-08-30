"""Content governance API routes."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status
from telethon import TelegramClient
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.telethon_runtime import (
    bootstrap_web_session,
    get_api_credentials,
    get_bot_config,
    open_web_client,
    resolve_web_session_path,
    safe_disconnect,
)
from app.routers import tools as tools_router
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/content-ops", tags=["content_ops"])

_config_manager: ConfigManager | None = None
_SCAN_JOBS_LOCK = threading.Lock()
_SCAN_BG_TASKS: dict[str, asyncio.Task[Any]] = {}
_SCAN_JOBS_KEEP_TOTAL = 80
_SCAN_JOBS_KEEP_WITH_ITEMS = 20


class MessageResponse(BaseModel):
    success: bool
    message: str


class BlacklistItem(BaseModel):
    keyword: str
    source: str = "manual"
    note: str = ""


class BlacklistListResponse(BaseModel):
    count: int
    items: list[BlacklistItem] = Field(default_factory=list)


class BlacklistAddRequest(BaseModel):
    keyword: str
    source: str | None = None
    note: str | None = None


class BlacklistImportRequest(BaseModel):
    entries: list[BlacklistAddRequest] = Field(default_factory=list)
    replace: bool = False


class BlacklistImportPreviewResponse(BaseModel):
    to_add: int
    to_update: int
    total_after_import: int


class AdMessageInput(BaseModel):
    message_id: str
    text: str
    chat_id: str | None = None
    has_media: bool = False


class AdScreenRequest(BaseModel):
    messages: list[AdMessageInput] = Field(default_factory=list)


class AdScreenItem(BaseModel):
    message_id: str
    text: str
    chat_id: str | None = None
    has_media: bool = False
    rule_hit: bool
    rule_reasons: list[str] = Field(default_factory=list)
    state: str
    suggested_action: str
    confidence: float


class AdScreenSummary(BaseModel):
    total: int
    rule_hit: int
    needs_ai: int


class AdScreenResponse(BaseModel):
    items: list[AdScreenItem] = Field(default_factory=list)
    summary: AdScreenSummary


class AdAnalyzeRequest(BaseModel):
    items: list[AdScreenItem] = Field(default_factory=list)


class AdAnalyzeItem(BaseModel):
    message_id: str
    text: str
    chat_id: str | None = None
    has_media: bool = False
    rule_hit: bool
    rule_reasons: list[str] = Field(default_factory=list)
    state: str
    suggested_action: str
    confidence: float
    ai_reason: str | None = None


class AdAnalyzeSummary(BaseModel):
    total: int
    block: int
    review: int
    allow: int


class AdAnalyzeResponse(BaseModel):
    items: list[AdAnalyzeItem] = Field(default_factory=list)
    summary: AdAnalyzeSummary


class AdDecisionCreateRequest(BaseModel):
    message_id: str
    text: str
    action: str
    state: str
    reason: str | None = None
    chat_id: str | None = None
    source: str = "ui"


class AdDecisionItem(BaseModel):
    decision_id: str
    created_at: int
    message_id: str
    text: str
    action: str
    state: str
    reason: str
    chat_id: str | None = None
    source: str = "ui"


class AdDecisionCreateResponse(BaseModel):
    success: bool
    item: AdDecisionItem


class AdDecisionListResponse(BaseModel):
    count: int
    items: list[AdDecisionItem] = Field(default_factory=list)


class RuleCandidateItem(BaseModel):
    candidate_id: str
    created_at: int
    status: str
    candidate_type: str
    value: str
    source_decision_id: str
    chat_id: str | None = None
    message_id: str | None = None
    note: str = ""


class RuleCandidateListResponse(BaseModel):
    count: int
    items: list[RuleCandidateItem] = Field(default_factory=list)


class ScanTargetItem(BaseModel):
    token: str
    title: str
    username: str = ""
    bot_can_manage: bool = False


class ScanTargetsResponse(BaseModel):
    success: bool = True
    count: int
    targets: list[ScanTargetItem] = Field(default_factory=list)
    model: str
    model_mode: str = "shared"


class ScanJobStartRequest(BaseModel):
    group_id: str
    scan_range: str = "latest_50"


class ScanResultItem(BaseModel):
    item_id: str
    chat_id: str
    text: str
    has_media: bool = False
    rule_hit: bool
    rule_reasons: list[str] = Field(default_factory=list)
    suggested_action: str
    confidence: float
    ai_reason: str = ""
    state: str


class ScanJobStartResponse(BaseModel):
    success: bool = True
    job_id: str
    group_id: str
    scan_range: str
    stage: str
    model: str
    model_mode: str = "shared"
    auto_processed_summary: dict[str, int]
    pending_human_review_count: int
    scanned_count: int = 0
    progress_percent: int = 0
    phase: str = "queued"
    eta_seconds: int = 0
    error: str = ""
    items: list[ScanResultItem] = Field(default_factory=list)


class ScanJobDetailResponse(ScanJobStartResponse):
    created_at: int


class ScanJobItemsResponse(BaseModel):
    total: int
    count: int
    offset: int
    limit: int
    items: list[ScanResultItem] = Field(default_factory=list)


class ScanResultActionRequest(BaseModel):
    job_id: str
    item_id: str
    action: str
    reason: str | None = None


class ScanResultActionResponse(BaseModel):
    success: bool = True
    job_id: str
    item_id: str
    action: str
    status: str
    message: str
    decision_id: str | None = None


def set_config_manager(manager: ConfigManager) -> None:
    global _config_manager
    _config_manager = manager


def _ensure_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _project_root() -> Path:
    cfg = _ensure_config_manager()
    config_path = Path(getattr(cfg, "config_path", "config.json"))
    return config_path.resolve().parent


def _blacklist_file() -> Path:
    return _project_root() / "data" / "content_ops" / "blacklist_keywords.txt"


def _decision_file() -> Path:
    return _project_root() / "data" / "content_ops" / "ad_decisions.jsonl"


def _rule_candidates_file() -> Path:
    return _project_root() / "data" / "content_ops" / "rule_candidates.jsonl"


def _scan_jobs_file() -> Path:
    return _project_root() / "data" / "content_ops" / "scan_jobs.json"


def _database_path(config: dict[str, Any]) -> Path:
    db_cfg = config.get("database", {}) if isinstance(config, dict) else {}
    raw = str(db_cfg.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()


def _clean_field(value: str | None) -> str:
    return str(value or "").replace("\t", " ").replace("\n", " ").strip()


def _load_blacklist() -> list[dict[str, str]]:
    path = _blacklist_file()
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        keyword = _clean_field(parts[0])
        if not keyword:
            continue
        source = _clean_field(parts[1] if len(parts) > 1 else "manual") or "manual"
        note = _clean_field(parts[2] if len(parts) > 2 else "")
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"keyword": keyword, "source": source, "note": note})
    rows.sort(key=lambda x: x["keyword"].casefold())
    return rows


def _save_blacklist(rows: list[dict[str, str]]) -> None:
    path = _blacklist_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    norm: dict[str, dict[str, str]] = {}
    for row in rows:
        keyword = _clean_field(row.get("keyword"))
        if not keyword:
            continue
        key = keyword.casefold()
        norm[key] = {
            "keyword": keyword,
            "source": _clean_field(row.get("source")) or "manual",
            "note": _clean_field(row.get("note")),
        }
    ordered = sorted(norm.values(), key=lambda x: x["keyword"].casefold())
    payload = "\n".join(f"{r['keyword']}\t{r['source']}\t{r['note']}" for r in ordered)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _blacklist_keywords_only(rows: list[dict[str, str]]) -> list[str]:
    return [str(r.get("keyword") or "") for r in rows if str(r.get("keyword") or "").strip()]


def _rule_screen(text: str, blacklist_keywords: list[str]) -> tuple[bool, list[str]]:
    lowered = text.casefold()
    reasons: list[str] = []

    for kw in blacklist_keywords:
        if kw and kw.casefold() in lowered:
            reasons.append(f"blacklist:{kw}")

    builtins = [
        (r"https?://", "url"),
        (r"t\\.me/", "telegram_link"),
        (r"(vx|v信|微信|qq|q群|飞机|电报)", "contact_hint"),
        (r"(送彩金|拉人|返利|推广|代理)", "ad_phrase"),
        (r"@[a-zA-Z0-9_]{4,}", "mention_link_hint"),
        (r"/[a-zA-Z0-9_]+@[a-zA-Z0-9_]{4,}", "bot_command_link"),
        (r"(点此加入|点击加入|点击进入|立即加入|加入频道|进群)", "join_cta"),
        (r"(神秘频道|特殊频道|禁止观看|福利视频|福利频道)", "adult_lure_phrase"),
    ]
    for pattern, tag in builtins:
        if re.search(pattern, text, flags=re.IGNORECASE):
            reasons.append(tag)

    channel_count = len(re.findall(r"频道\s*\d+", text, flags=re.IGNORECASE))
    if channel_count >= 2:
        reasons.append("channel_list_pattern")
    cta_count = len(re.findall(r"(点此加入|点击加入|点击进入|立即加入)", text, flags=re.IGNORECASE))
    if cta_count >= 2:
        reasons.append("repeated_join_cta")

    adult_signal_count = len(re.findall(r"(里番|成人|求片)", text, flags=re.IGNORECASE))
    channel_token_count = len(re.findall(r"频道", text, flags=re.IGNORECASE))
    variant_count = len(re.findall(r"(福利版|求片版|共创版|vip版)", text, flags=re.IGNORECASE))
    matrix_line_count = 0
    for line in [x.strip() for x in text.splitlines() if x.strip()]:
        if re.search(r"(频道|福利版|求片版|共创版|里番|成人)", line, flags=re.IGNORECASE):
            matrix_line_count += 1

    if adult_signal_count >= 1 and channel_token_count >= 1 and variant_count >= 1:
        reasons.append("adult_variant_lure")
    if adult_signal_count >= 1 and channel_token_count >= 1 and matrix_line_count >= 3:
        reasons.append("adult_channel_matrix")

    unique = sorted(set(reasons))
    return (len(unique) > 0), unique


def _provider_secrets_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "provider_secrets"


def _read_provider_secret(provider_key: str) -> dict[str, Any]:
    key = _clean_field(provider_key).lower()
    if not key:
        return {}
    path = _provider_secrets_dir() / f"{key}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _parse_json_from_text(content: str) -> dict[str, Any] | None:
    raw = str(content or "").strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    if "```" in raw:
        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE):
            candidates.append(block.strip())
    for item in candidates:
        try:
            obj = json.loads(item)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _try_provider_second_pass(*, item: AdScreenItem, config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        db = Database(_database_path(config))
    except Exception:
        return None
    try:
        providers = db.list_providers(enabled_only=True)
    except Exception:
        providers = []
    finally:
        db.close()

    if not providers:
        return None

    provider = providers[0]
    provider_key = _clean_field(str(provider.get("provider_key") or ""))
    model = _clean_field(str(provider.get("default_model") or ""))
    base_url = _clean_field(str(provider.get("base_url") or ""))
    if not provider_key or not model or not base_url:
        return None

    secret = _read_provider_secret(provider_key)
    api_key = _clean_field(str(secret.get("api_key") or ""))
    if not api_key:
        return None

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    prompt = (
        "你是群广告识别器。只输出 JSON，不要额外文字。\n"
        "字段: is_ad(bool), confidence(float), recommended_action(str in block/review/allow), "
        "reason(str), keep_media(bool).\n"
        f"has_media={str(bool(item.has_media)).lower()}\n"
        f"text=\"\"\"{item.text}\"\"\""
    )
    req_payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "你是广告内容审核助手"},
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        endpoint,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "content-ops-ai-second-pass/1.0",
        },
        data=json.dumps(req_payload).encode("utf-8"),
    )
    timeout_seconds = max(1.0, float(config.get("ai_runtime", {}).get("timeout_ms", 8000)) / 1000.0) if isinstance(config.get("ai_runtime"), dict) else 8.0
    try:
        with urlopen(request, timeout=timeout_seconds) as resp:  # nosec B310
            body = resp.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    try:
        payload = json.loads(body)
    except Exception:
        return None

    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content") if isinstance(msg, dict) else ""
    parsed = _parse_json_from_text(str(content or ""))
    if not parsed:
        return None

    action = _clean_field(str(parsed.get("recommended_action") or "")).lower()
    if action not in {"block", "review", "allow"}:
        action = "review"
    conf_raw = parsed.get("confidence", 0.7)
    try:
        confidence = max(0.0, min(float(conf_raw), 1.0))
    except Exception:
        confidence = 0.7
    reason = _clean_field(str(parsed.get("reason") or "provider_second_pass")) or "provider_second_pass"
    return {
        "suggested_action": action,
        "state": "blocked" if action == "block" else ("review" if action == "review" else "allowed"),
        "confidence": confidence,
        "ai_reason": reason,
    }


def _ai_second_pass(item: AdScreenItem, *, config: dict[str, Any] | None = None) -> AdAnalyzeItem:
    base = item.model_dump()
    base.pop("state", None)
    base.pop("suggested_action", None)
    base.pop("confidence", None)

    if item.rule_hit:
        return AdAnalyzeItem(
            **base,
            state="blocked",
            suggested_action="block",
            confidence=max(float(item.confidence), 0.95),
            ai_reason="规则强命中，按分级策略直接拦截",
        )

    if config is not None:
        provider_result = _try_provider_second_pass(item=item, config=config)
        if provider_result is not None:
            return AdAnalyzeItem(
                **base,
                state=str(provider_result.get("state") or "review"),
                suggested_action=str(provider_result.get("suggested_action") or "review"),
                confidence=float(provider_result.get("confidence") or 0.7),
                ai_reason=str(provider_result.get("ai_reason") or "provider_second_pass"),
            )

    text = item.text.casefold()
    score = 0
    for token in ["加群", "私聊", "代理", "返利", "兼职", "稳赚", "带你", "频道", "点此加入", "点击加入", "点击进入", "神秘", "禁止观看", "福利"]:
        if token in text:
            score += 1

    if score >= 4:
        decision = "review" if item.has_media else "block"
        state = "review" if item.has_media else "blocked"
        return AdAnalyzeItem(
            **base,
            state=state,
            suggested_action=decision,
            confidence=0.9,
            ai_reason="AI 识别为高风险导流广告",
        )

    if score >= 2:
        return AdAnalyzeItem(
            **base,
            state="review",
            suggested_action="review",
            confidence=0.86,
            ai_reason="AI 识别为疑似广告，高置信进入人工审核",
        )

    return AdAnalyzeItem(
        **base,
        state="allowed",
        suggested_action="allow",
        confidence=0.3,
        ai_reason="AI 识别风险较低，建议放行",
    )


def _append_decision(item: AdDecisionItem) -> None:
    path = _decision_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")


def _extract_rule_candidates(text: str) -> list[tuple[str, str]]:
    value = str(text or "")
    pairs: list[tuple[str, str]] = []

    for m in re.findall(r"https?://[^\s]+", value, flags=re.IGNORECASE):
        pairs.append(("url", m.strip()))
    for m in re.findall(r"t\.me/[^\s]+", value, flags=re.IGNORECASE):
        pairs.append(("telegram_link", m.strip()))
    for m in re.findall(r"@[a-zA-Z0-9_]{4,}", value):
        pairs.append(("mention", m.strip()))
    for m in re.findall(r"/[a-zA-Z0-9_]+@[a-zA-Z0-9_]{4,}", value):
        pairs.append(("bot_command", m.strip()))
    for m in re.findall(r"#[^\s#]{2,}", value):
        pairs.append(("hashtag", m.strip()))

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for t, v in pairs:
        key = (t, v.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append((t, v))
    return out


def _append_rule_candidates_from_decision(*, decision: AdDecisionItem, message_text: str) -> int:
    if decision.action not in {"block", "sanitize"}:
        return 0
    items = _extract_rule_candidates(message_text)
    if not items:
        return 0

    path = _rule_candidates_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    created = 0
    now = int(time.time())
    with path.open("a", encoding="utf-8") as fh:
        for candidate_type, value in items:
            row = {
                "candidate_id": f"cand_{now}_{uuid.uuid4().hex[:8]}",
                "created_at": now,
                "status": "pending",
                "candidate_type": candidate_type,
                "value": value,
                "source_decision_id": decision.decision_id,
                "chat_id": decision.chat_id,
                "message_id": decision.message_id,
                "note": decision.reason,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            created += 1
    return created


def _append_false_positive_candidate_from_decision(*, decision: AdDecisionItem, message_text: str) -> int:
    sample = _clean_field(message_text)
    if not sample:
        return 0

    value = re.sub(r"\s+", " ", sample).strip()[:160]
    if not value:
        return 0

    now = int(time.time())
    path = _rule_candidates_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "candidate_id": f"cand_{now}_{uuid.uuid4().hex[:8]}",
        "created_at": now,
        "status": "pending",
        "candidate_type": "false_positive_text",
        "value": value,
        "source_decision_id": decision.decision_id,
        "chat_id": decision.chat_id,
        "message_id": decision.message_id,
        "note": "false_positive_feedback",
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 1


def _list_rule_candidates(*, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    path = _rule_candidates_file()
    if not path.exists():
        return []
    target_status = _clean_field(status).lower() if status else ""
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if target_status and str(row.get("status") or "").lower() != target_status:
            continue
        rows.append(row)
    rows.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
    return rows[: max(1, min(int(limit or 50), 500))]


def _update_rule_candidate_status(*, candidate_id: str, status_value: str) -> dict[str, Any] | None:
    path = _rule_candidates_file()
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("candidate_id") or "") == candidate_id:
            row["status"] = status_value
            row["updated_at"] = int(time.time())
            found = row
        rows.append(row)

    if found is None:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    return found


def _approve_rule_candidate_to_blacklist(candidate: dict[str, Any]) -> str:
    candidate_type = _clean_field(str(candidate.get("candidate_type") or "")).lower()
    value = _clean_field(str(candidate.get("value") or ""))
    if candidate_type not in {"mention", "url", "telegram_link", "bot_command", "hashtag"}:
        return "candidate_type_not_supported"
    if not value:
        return "candidate_value_empty"

    rows = _load_blacklist()
    exists = any(_clean_field(r.get("keyword")).casefold() == value.casefold() for r in rows)
    if exists:
        return "blacklist_already_exists"
    rows.append({
        "keyword": value,
        "source": "candidate_approved",
        "note": f"from:{_clean_field(str(candidate.get('candidate_id') or ''))}",
    })
    _save_blacklist(rows)
    return "blacklist_added"


def _list_decisions(*, limit: int, action: str | None = None, since_ts: int | None = None) -> list[dict]:
    path = _decision_file()
    if not path.exists():
        return []
    rows: list[dict] = []
    target_action = _clean_field(action).lower() if action else ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if target_action and str(row.get("action") or "").lower() != target_action:
            continue
        created_at = int(row.get("created_at") or 0)
        if since_ts is not None and created_at < int(since_ts):
            continue
        rows.append(row)
    rows.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
    return rows[: max(1, min(int(limit or 50), 200))]


def _load_scan_jobs() -> dict[str, dict[str, Any]]:
    with _SCAN_JOBS_LOCK:
        path = _scan_jobs_file()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        now = int(time.time())
        changed = False
        stale_seconds = 15 * 60
        for k, v in payload.items():
            if not (isinstance(k, str) and isinstance(v, dict)):
                continue
            row = dict(v)
            stage = str(row.get("stage") or "")
            if not _clean_field(str(row.get("phase") or "")):
                row["phase"] = stage or "queued"
                changed = True
            if stage == "running":
                heartbeat = int(row.get("updated_at") or row.get("created_at") or 0)
                if heartbeat > 0 and (now - heartbeat) > stale_seconds:
                    row["stage"] = "failed"
                    row["phase"] = "failed"
                    row["eta_seconds"] = 0
                    row["error"] = "scan_worker_stale_timeout"
                    row["updated_at"] = now
                    changed = True
            out[k] = row

        running_keys: list[str] = []
        terminal_keys: list[str] = []
        for k, row in out.items():
            stage = str(row.get("stage") or "")
            if stage == "running":
                running_keys.append(k)
            else:
                terminal_keys.append(k)

        terminal_keys.sort(key=lambda key: int(out.get(key, {}).get("created_at") or 0), reverse=True)

        keep_terminal = set(terminal_keys[:_SCAN_JOBS_KEEP_TOTAL])
        keep_keys = set(running_keys) | keep_terminal

        if len(keep_keys) != len(out):
            out = {k: v for k, v in out.items() if k in keep_keys}
            changed = True

        for idx, key in enumerate(terminal_keys):
            if idx < _SCAN_JOBS_KEEP_WITH_ITEMS:
                continue
            row = out.get(key)
            if row is None:
                continue
            items = row.get("items")
            if isinstance(items, list) and items:
                row["items"] = []
                row["updated_at"] = now
                out[key] = row
                changed = True

        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out


def _save_scan_jobs(rows: dict[str, dict[str, Any]]) -> None:
    with _SCAN_JOBS_LOCK:
        path = _scan_jobs_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_range_to_limit(scan_range: str) -> int:
    value = _clean_field(scan_range).lower()
    if value in {"latest_20", "20", "latest20"}:
        return 20
    if value in {"latest_50", "50", "latest50"}:
        return 50
    if value in {"latest_100", "100", "latest100"}:
        return 100
    if value in {"latest_300", "300", "latest300"}:
        return 300
    if value in {"full_accessible", "full", "all", "latest_all"}:
        return 3000
    if value.startswith("latest_"):
        raw = value.replace("latest_", "", 1)
        if raw.isdigit():
            n = int(raw)
            return max(1, min(n, 3000))
    if value.isdigit():
        n = int(value)
        return max(1, min(n, 3000))
    return 50


def _detect_global_model(config: dict[str, Any]) -> str:
    try:
        db = Database(_database_path(config))
    except Exception:
        return "global-default"
    try:
        providers = db.list_providers(enabled_only=True)
    except Exception:
        return "global-default"
    finally:
        db.close()
    if not providers:
        return "global-default"
    first = providers[0]
    provider_key = str(first.get("provider_key") or "default").strip()
    default_model = str(first.get("default_model") or "").strip()
    return f"{provider_key}:{default_model}" if default_model else provider_key


async def _fetch_recent_messages(*, group_id: str, limit: int) -> list[AdMessageInput]:
    cfg = _ensure_config_manager()
    config = cfg.get_config()
    bot_config = get_bot_config(config)
    target = _clean_field(group_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_id 不能为空")

    try:
        async with _open_scan_client(bot_config) as client:
            entity = await client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
            rows: list[AdMessageInput] = []
            async for msg in client.iter_messages(entity, limit=max(1, min(limit, 3000))):
                base_text = str(getattr(msg, "message", "") or "").strip()
                signals: list[str] = []
                entities = list(getattr(msg, "entities", []) or []) + list(getattr(msg, "caption_entities", []) or [])
                for ent in entities:
                    url = str(getattr(ent, "url", "") or "").strip()
                    if url:
                        signals.append(url)
                markup = getattr(msg, "reply_markup", None)
                rows_markup = list(getattr(markup, "rows", []) or [])
                for r in rows_markup:
                    for btn in list(getattr(r, "buttons", []) or []):
                        u = str(getattr(btn, "url", "") or "").strip()
                        if u:
                            signals.append(u)
                merged = "\n".join(x for x in [base_text] + signals if x).strip()
                if not merged:
                    continue
                rows.append(
                    AdMessageInput(
                        message_id=str(getattr(msg, "id", "")),
                        text=merged,
                        chat_id=target,
                        has_media=bool(getattr(msg, "media", None)),
                    )
                )
            return rows
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"读取目标群组消息失败: {exc}") from exc


def _sanitize_text(text: str, blacklist_keywords: list[str]) -> str:
    value = str(text or "")
    value = re.sub(r"https?://\S+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"t\.me/\S+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"/[a-zA-Z0-9_]+@[a-zA-Z0-9_]{4,}", "", value, flags=re.IGNORECASE)
    value = re.sub(r"@[a-zA-Z0-9_]{4,}", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(vx|v信|微信|qq|q群|飞机|电报)\s*[:：]?\s*\S+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*[\W_]*频道\s*\d+\s*[：:]?\s*(点此加入|点击加入|点击进入|立即加入).*$", "", value, flags=re.IGNORECASE | re.MULTILINE)
    value = re.sub(r"(点此加入|点击加入|点击进入|立即加入)", "", value, flags=re.IGNORECASE)
    for kw in blacklist_keywords:
        if not kw:
            continue
        value = re.sub(re.escape(kw), "", value, flags=re.IGNORECASE)
    value = re.sub(r"\n{2,}", "\n", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "【净化后内容已清空】"


async def _apply_result_action(*, action: str, chat_id: str, item_id: str, text: str) -> tuple[str, str]:
    action_value = _clean_field(action).lower()
    if action_value == "allow":
        return "allowed", "已放行（未执行远端操作）"
    if action_value == "review":
        return "review", "已加入人工复核"

    cfg = _ensure_config_manager()
    config = cfg.get_config()
    bot_config = get_bot_config(config)
    msg_id = int(str(item_id).strip())
    target = _clean_field(chat_id)

    blacklist = _blacklist_keywords_only(_load_blacklist())

    try:
        async with _open_scan_client(bot_config) as client:
            entity = await client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
            if action_value == "block":
                try:
                    await client.delete_messages(entity, [msg_id])
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"执行拦截失败: {exc}") from exc
                return "blocked", "已执行拦截（删除原消息）"
            if action_value == "sanitize":
                try:
                    source_msg = await client.get_messages(entity, ids=msg_id)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"读取原消息失败: {exc}") from exc

                source_text = str(getattr(source_msg, "message", "") or text or "")
                cleaned = _sanitize_text(source_text, blacklist)
                if getattr(source_msg, "media", None):
                    caption = "" if cleaned == "【净化后内容已清空】" else cleaned
                    try:
                        await client.send_file(entity, source_msg.media, caption=caption)
                    except Exception as exc:  # noqa: BLE001
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"媒体净化失败(重发媒体失败): {exc}") from exc
                    try:
                        await client.delete_messages(entity, [msg_id])
                    except Exception as exc:  # noqa: BLE001
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"媒体净化失败(删除原消息失败): {exc}") from exc
                    return "sanitized_keep_media", "已保留媒体并完成文案净化"

                try:
                    await client.delete_messages(entity, [msg_id])
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"纯文本广告删除失败: {exc}") from exc
                return "blocked", "纯文本广告已删除"
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"执行动作失败: {exc}") from exc

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"不支持的动作: {action_value}")


def _load_manageable_targets(config: dict[str, Any]) -> list[ScanTargetItem]:
    db_path = _database_path(config)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT chat_id, title, username, bot_can_manage
            FROM managed_chats
            ORDER BY title COLLATE NOCASE ASC, chat_id DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    items: list[ScanTargetItem] = []
    seen: set[str] = set()
    for row in rows:
        token = str(row["chat_id"]).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        can_manage = bool(row["bot_can_manage"])
        items.append(
            ScanTargetItem(
                token=token,
                title=str(row["title"] or token),
                username=str(row["username"] or ""),
                bot_can_manage=can_manage,
            )
        )
    return [x for x in items if x.bot_can_manage]


@router.get("/scan-targets", response_model=ScanTargetsResponse)
async def list_scan_targets(_: Annotated[str, Depends(get_current_user)]) -> ScanTargetsResponse:
    cfg = _ensure_config_manager()
    config = cfg.get_config()
    targets = _load_manageable_targets(config)
    model = _detect_global_model(config)
    return ScanTargetsResponse(count=len(targets), targets=targets, model=model)


def _is_async_scan_mode(scan_range: str, limit: int) -> bool:
    _ = scan_range
    _ = limit
    return True


def _estimate_eta_seconds(*, scanned_count: int, max_limit: int, started_at: int) -> int:
    if scanned_count <= 0 or max_limit <= 0:
        return 0
    elapsed = max(1, int(time.time()) - int(started_at or int(time.time())))
    rate = float(scanned_count) / float(elapsed)
    if rate <= 0:
        return 0
    remaining = max(0, int(max_limit) - int(scanned_count))
    return max(0, int(remaining / rate))


async def _build_scan_result_items(*, group_id: str, limit: int, config: dict[str, Any]) -> tuple[list[ScanResultItem], int]:
    if limit <= 300:
        try:
            await tools_router._send_command(f"/scan {limit}", requested_target=group_id)
        except Exception:
            pass

    messages = await _fetch_recent_messages(group_id=group_id, limit=limit)
    blacklist = _blacklist_keywords_only(_load_blacklist())
    screened: list[AdScreenItem] = []
    for msg in messages:
        hit, reasons = _rule_screen(msg.text, blacklist)
        screened.append(
            AdScreenItem(
                message_id=msg.message_id,
                text=msg.text,
                chat_id=msg.chat_id,
                has_media=bool(getattr(msg, "has_media", False)),
                rule_hit=hit,
                rule_reasons=reasons,
                state="rule_screened" if hit else "needs_ai",
                suggested_action="block" if hit else "needs_ai",
                confidence=0.96 if hit else 0.35,
            )
        )

    analyzed = [_ai_second_pass(item, config=config) for item in screened]
    result_items = [
        ScanResultItem(
            item_id=str(item.message_id),
            chat_id=str(item.chat_id or group_id),
            text=item.text,
            has_media=bool(item.has_media),
            rule_hit=item.rule_hit,
            rule_reasons=item.rule_reasons,
            suggested_action=item.suggested_action,
            confidence=float(item.confidence),
            ai_reason=str(item.ai_reason or ""),
            state=item.state,
        )
        for item in analyzed
    ]
    return result_items, len(messages)


def _session_db_path(base: Path) -> Path:
    return Path(f"{base}.session")


def _copy_session_db(src_base: Path, dst_base: Path) -> None:
    src_db = _session_db_path(src_base)
    dst_db = _session_db_path(dst_base)
    if not src_db.exists() or dst_db.exists():
        return
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dst_db)
    for suffix in ("-journal", "-wal", "-shm"):
        src_aux = Path(f"{src_db}{suffix}")
        dst_aux = Path(f"{dst_db}{suffix}")
        if src_aux.exists() and not dst_aux.exists():
            shutil.copy2(src_aux, dst_aux)


@asynccontextmanager
async def _open_scan_client(bot_config: dict[str, Any]) -> Any:
    api_id, api_hash = get_api_credentials(bot_config)
    web_base = resolve_web_session_path(bot_config)
    bootstrap_web_session(bot_config, web_base)
    scan_base = Path(str(web_base) + "_scan")
    _copy_session_db(web_base, scan_base)

    client = TelegramClient(str(scan_base), api_id, api_hash)
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=30):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="扫描会话未登录，请先在账号管理登录")
        me = await asyncio.wait_for(client.get_me(), timeout=30)
        if getattr(me, "bot", False):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="扫描会话不能是 Bot 账号")
        yield client
    finally:
        await safe_disconnect(client)


async def _run_scan_job_background(*, job_id: str, group_id: str, scan_range: str, limit: int, config: dict[str, Any]) -> None:
    target = _clean_field(group_id)
    if not target:
        jobs = _load_scan_jobs()
        row = jobs.get(job_id, {})
        row.update({"stage": "failed", "phase": "failed", "eta_seconds": 0, "error": "group_id 不能为空"})
        jobs[job_id] = row
        _save_scan_jobs(jobs)
        return

    max_limit = max(1, min(int(limit or 3000), 3000))
    bot_config = get_bot_config(config)
    blacklist = _blacklist_keywords_only(_load_blacklist())
    result_items: list[ScanResultItem] = []
    scanned_count = 0
    block_count = 0
    allow_count = 0
    review_count = 0
    offset_id = 0
    batch_size = 20

    jobs = _load_scan_jobs()
    seed = jobs.get(job_id, {})
    started_at = int(seed.get("created_at") or time.time())
    seed.update({
        "stage": "running",
        "phase": "connecting",
        "eta_seconds": 0,
        "updated_at": int(time.time()),
    })
    jobs[job_id] = seed
    _save_scan_jobs(jobs)

    try:
        async with _open_scan_client(bot_config) as client:
            entity = await client.get_entity(int(target) if target.lstrip("-").isdigit() else target)

            jobs = _load_scan_jobs()
            row = jobs.get(job_id, {})
            row.update({"phase": "fetching", "updated_at": int(time.time())})
            jobs[job_id] = row
            _save_scan_jobs(jobs)

            while scanned_count < max_limit:
                jobs = _load_scan_jobs()
                row = jobs.get(job_id, {})
                if bool(row.get("cancel_requested")):
                    row.update({
                        "stage": "cancelled",
                        "phase": "cancelled",
                        "error": "",
                        "scanned_count": scanned_count,
                        "progress_percent": min(99, int((scanned_count * 100) / max_limit)) if max_limit > 0 else 0,
                        "eta_seconds": 0,
                        "auto_processed_summary": {
                            "block": block_count,
                            "allow": allow_count,
                            "total_auto_processed": block_count + allow_count,
                        },
                        "pending_human_review_count": review_count,
                        "items": [x.model_dump() for x in result_items],
                    })
                    jobs[job_id] = row
                    _save_scan_jobs(jobs)
                    return

                chunk_limit = min(batch_size, max_limit - scanned_count)
                batch_rows: list[AdMessageInput] = []
                last_msg_id = 0
                async for msg in client.iter_messages(entity, limit=chunk_limit, offset_id=offset_id):
                    mid = int(getattr(msg, "id", 0) or 0)
                    if mid > 0:
                        last_msg_id = mid
                    base_text = str(getattr(msg, "message", "") or "").strip()
                    signals: list[str] = []
                    entities = list(getattr(msg, "entities", []) or []) + list(getattr(msg, "caption_entities", []) or [])
                    for ent in entities:
                        url = str(getattr(ent, "url", "") or "").strip()
                        if url:
                            signals.append(url)
                    markup = getattr(msg, "reply_markup", None)
                    rows_markup = list(getattr(markup, "rows", []) or [])
                    for r in rows_markup:
                        for btn in list(getattr(r, "buttons", []) or []):
                            u = str(getattr(btn, "url", "") or "").strip()
                            if u:
                                signals.append(u)
                    merged = "\n".join(x for x in [base_text] + signals if x).strip()
                    if not merged:
                        continue
                    batch_rows.append(
                        AdMessageInput(
                            message_id=str(mid or getattr(msg, "id", "")),
                            text=merged,
                            chat_id=target,
                            has_media=bool(getattr(msg, "media", None)),
                        )
                    )

                if last_msg_id > 0:
                    offset_id = last_msg_id

                if not batch_rows:
                    break

                screened: list[AdScreenItem] = []
                for row_msg in batch_rows:
                    hit, reasons = _rule_screen(row_msg.text, blacklist)
                    screened.append(
                        AdScreenItem(
                            message_id=row_msg.message_id,
                            text=row_msg.text,
                            chat_id=row_msg.chat_id,
                            has_media=bool(getattr(row_msg, "has_media", False)),
                            rule_hit=hit,
                            rule_reasons=reasons,
                            state="rule_screened" if hit else "needs_ai",
                            suggested_action="block" if hit else "needs_ai",
                            confidence=0.96 if hit else 0.35,
                        )
                    )

                jobs = _load_scan_jobs()
                row = jobs.get(job_id, {})
                row.update({"phase": "analyzing", "updated_at": int(time.time())})
                jobs[job_id] = row
                _save_scan_jobs(jobs)

                analyzed = [_ai_second_pass(item, config=config) for item in screened]
                for item in analyzed:
                    result = ScanResultItem(
                        item_id=str(item.message_id),
                        chat_id=str(item.chat_id or group_id),
                        text=item.text,
                        has_media=bool(item.has_media),
                        rule_hit=item.rule_hit,
                        rule_reasons=item.rule_reasons,
                        suggested_action=item.suggested_action,
                        confidence=float(item.confidence),
                        ai_reason=str(item.ai_reason or ""),
                        state=item.state,
                    )
                    result_items.append(result)
                    if result.suggested_action == "block":
                        block_count += 1
                    elif result.suggested_action == "allow":
                        allow_count += 1
                    elif result.suggested_action == "review":
                        review_count += 1

                scanned_count += len(batch_rows)
                progress = min(99, int((scanned_count * 100) / max_limit)) if max_limit > 0 else 0
                jobs = _load_scan_jobs()
                row = jobs.get(job_id, {})
                row.update({
                    "stage": "running",
                    "phase": "running",
                    "error": "",
                    "scanned_count": scanned_count,
                    "progress_percent": progress,
                    "eta_seconds": _estimate_eta_seconds(scanned_count=scanned_count, max_limit=max_limit, started_at=started_at),
                    "auto_processed_summary": {
                        "block": block_count,
                        "allow": allow_count,
                        "total_auto_processed": block_count + allow_count,
                    },
                    "pending_human_review_count": review_count,
                    "updated_at": int(time.time()),
                })
                jobs[job_id] = row
                _save_scan_jobs(jobs)

        jobs = _load_scan_jobs()
        row = jobs.get(job_id, {})
        row.update({
            "stage": "completed",
            "phase": "completed",
            "error": "",
            "scanned_count": scanned_count,
            "progress_percent": 100,
            "eta_seconds": 0,
            "auto_processed_summary": {
                "block": block_count,
                "allow": allow_count,
                "total_auto_processed": block_count + allow_count,
            },
            "pending_human_review_count": review_count,
            "updated_at": int(time.time()),
            "items": [x.model_dump() for x in result_items],
        })
        jobs[job_id] = row
        _save_scan_jobs(jobs)
    except asyncio.CancelledError:
        jobs = _load_scan_jobs()
        row = jobs.get(job_id, {})
        row.update({
            "stage": "cancelled",
            "phase": "cancelled",
            "error": "",
            "eta_seconds": 0,
            "cancel_requested": True,
            "updated_at": int(time.time()),
        })
        jobs[job_id] = row
        _save_scan_jobs(jobs)
        return
    except Exception as exc:  # noqa: BLE001
        jobs = _load_scan_jobs()
        row = jobs.get(job_id, {})
        row.update({
            "stage": "failed",
            "phase": "failed",
            "error": _clean_field(str(exc)) or "scan_failed",
            "eta_seconds": 0,
            "updated_at": int(time.time()),
        })
        jobs[job_id] = row
        _save_scan_jobs(jobs)


def _spawn_scan_job_thread(*, job_id: str, group_id: str, scan_range: str, limit: int, config: dict[str, Any]) -> None:
    def _runner() -> None:
        asyncio.run(_run_scan_job_background(job_id=job_id, group_id=group_id, scan_range=scan_range, limit=limit, config=config))

    thread = threading.Thread(target=_runner, daemon=True, name=f"scan-job-{job_id[:12]}")
    thread.start()


@router.post("/scan-jobs", response_model=ScanJobStartResponse)
async def start_scan_job(
    payload: ScanJobStartRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> ScanJobStartResponse:
    cfg = _ensure_config_manager()
    config = cfg.get_config()
    group_id = _clean_field(payload.group_id)
    if not group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_id 不能为空")

    manageable = {row.token for row in _load_manageable_targets(config)}
    if manageable and group_id not in manageable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="目标群组不可维护，请确认 Bot 权限")

    scan_range = _clean_field(payload.scan_range) or "latest_50"
    limit = _scan_range_to_limit(scan_range)

    existing_jobs = _load_scan_jobs()
    running = [jid for jid, jrow in existing_jobs.items() if str(jrow.get("stage") or "") == "running"]
    if running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"已有扫描任务运行中，请稍后重试（job_id={running[0]}）",
        )

    job_id = f"scan_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    model = _detect_global_model(config)
    created_at = int(time.time())

    if _is_async_scan_mode(scan_range, limit):
        job_payload = {
            "job_id": job_id,
            "group_id": group_id,
            "scan_range": scan_range,
            "stage": "running",
            "model": model,
            "model_mode": "shared",
            "auto_processed_summary": {"block": 0, "allow": 0, "total_auto_processed": 0},
            "pending_human_review_count": 0,
            "scanned_count": 0,
            "progress_percent": 0,
            "phase": "queued",
            "eta_seconds": 0,
            "error": "",
            "created_at": created_at,
            "updated_at": created_at,
            "items": [],
        }
        jobs = _load_scan_jobs()
        jobs[job_id] = job_payload
        _save_scan_jobs(jobs)
        _spawn_scan_job_thread(job_id=job_id, group_id=group_id, scan_range=scan_range, limit=limit, config=dict(config))
        return ScanJobStartResponse(**{k: v for k, v in job_payload.items() if k != "created_at"})

    result_items, scanned_count = await _build_scan_result_items(group_id=group_id, limit=limit, config=config)
    block = sum(1 for i in result_items if i.suggested_action == "block")
    review = sum(1 for i in result_items if i.suggested_action == "review")
    allow = sum(1 for i in result_items if i.suggested_action == "allow")

    job_payload = {
        "job_id": job_id,
        "group_id": group_id,
        "scan_range": scan_range,
        "stage": "completed",
        "model": model,
        "model_mode": "shared",
        "auto_processed_summary": {
            "block": block,
            "allow": allow,
            "total_auto_processed": block + allow,
        },
        "pending_human_review_count": review,
        "scanned_count": scanned_count,
        "progress_percent": 100,
        "phase": "completed",
        "eta_seconds": 0,
        "error": "",
        "created_at": created_at,
        "updated_at": created_at,
        "items": [item.model_dump() for item in result_items],
    }

    jobs = _load_scan_jobs()
    jobs[job_id] = job_payload
    _save_scan_jobs(jobs)

    return ScanJobStartResponse(**{k: v for k, v in job_payload.items() if k != "created_at"})


@router.get("/scan-jobs/{job_id}", response_model=ScanJobDetailResponse)
async def get_scan_job(
    job_id: str,
    include_items: bool = False,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> ScanJobDetailResponse:
    jobs = _load_scan_jobs()
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan job not found")
    payload = dict(row)
    if not include_items:
        payload["items"] = []
    return ScanJobDetailResponse(**payload)


@router.get("/scan-jobs/{job_id}/items", response_model=ScanJobItemsResponse)
async def get_scan_job_items(
    job_id: str,
    offset: int = 0,
    limit: int = 200,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> ScanJobItemsResponse:
    jobs = _load_scan_jobs()
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan job not found")
    all_items = row.get("items", []) if isinstance(row.get("items"), list) else []
    start = max(0, int(offset or 0))
    size = max(1, min(int(limit or 200), 1000))
    chunk = all_items[start : start + size]
    return ScanJobItemsResponse(
        total=len(all_items),
        count=len(chunk),
        offset=start,
        limit=size,
        items=[ScanResultItem(**x) if isinstance(x, dict) else x for x in chunk],
    )


@router.post("/scan-jobs/{job_id}/cancel", response_model=MessageResponse)
async def cancel_scan_job(job_id: str, _: Annotated[str, Depends(get_current_user)]) -> MessageResponse:
    jobs = _load_scan_jobs()
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan job not found")

    stage = str(row.get("stage") or "")
    if stage in {"completed", "failed", "cancelled"}:
        return MessageResponse(success=True, message=f"任务当前状态为 {stage}，无需取消")

    row["cancel_requested"] = True
    row["updated_at"] = int(time.time())
    jobs[job_id] = row
    _save_scan_jobs(jobs)

    return MessageResponse(success=True, message="已请求取消扫描任务")


@router.post("/result-actions", response_model=ScanResultActionResponse)
async def apply_result_action(
    payload: ScanResultActionRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> ScanResultActionResponse:
    requested_action = _clean_field(payload.action).lower()
    effective_action = "review" if requested_action == "false_positive" else requested_action

    jobs = _load_scan_jobs()
    row = jobs.get(payload.job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scan job not found")

    items = row.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid job items")

    found: dict[str, Any] | None = None
    for item in items:
        if str(item.get("item_id") or "") == str(payload.item_id):
            found = item
            break
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="result item not found")

    status_value, message = await _apply_result_action(
        action=effective_action,
        chat_id=str(found.get("chat_id") or row.get("group_id") or ""),
        item_id=str(found.get("item_id") or ""),
        text=str(found.get("text") or ""),
    )

    decision = AdDecisionItem(
        decision_id=f"dec_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        created_at=int(time.time()),
        message_id=str(found.get("item_id") or ""),
        text=str(found.get("text") or ""),
        action=effective_action or "review",
        state=status_value,
        reason=_clean_field(payload.reason) or message,
        chat_id=str(found.get("chat_id") or row.get("group_id") or "") or None,
        source="ui_real_scan",
    )
    _append_decision(decision)
    _append_rule_candidates_from_decision(decision=decision, message_text=str(found.get("text") or ""))
    if requested_action == "false_positive":
        _append_false_positive_candidate_from_decision(decision=decision, message_text=str(found.get("text") or ""))
        message = "已标记为误伤样本，进入规则复盘"

    found["state"] = status_value
    if status_value == "allowed":
        found["suggested_action"] = "allow"
    elif status_value == "blocked":
        found["suggested_action"] = "block"
    elif status_value == "review":
        found["suggested_action"] = "review"
    else:
        found["suggested_action"] = "sanitize"
    row["items"] = items
    jobs[payload.job_id] = row
    _save_scan_jobs(jobs)

    return ScanResultActionResponse(
        job_id=payload.job_id,
        item_id=str(payload.item_id),
        action=requested_action or "",
        status=status_value,
        message=message,
        decision_id=decision.decision_id,
    )


@router.get("/blacklist", response_model=BlacklistListResponse)
async def list_blacklist_keywords(_: Annotated[str, Depends(get_current_user)]) -> BlacklistListResponse:
    _ensure_config_manager()
    rows = _load_blacklist()
    return BlacklistListResponse(count=len(rows), items=[BlacklistItem(**r) for r in rows])


@router.get("/blacklist/export", response_model=BlacklistListResponse)
async def export_blacklist_keywords(_: Annotated[str, Depends(get_current_user)]) -> BlacklistListResponse:
    _ensure_config_manager()
    rows = _load_blacklist()
    return BlacklistListResponse(count=len(rows), items=[BlacklistItem(**r) for r in rows])


@router.post("/blacklist/import/preview", response_model=BlacklistImportPreviewResponse)
async def preview_blacklist_import(
    payload: BlacklistImportRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> BlacklistImportPreviewResponse:
    _ensure_config_manager()
    base_rows = [] if payload.replace else _load_blacklist()
    base_map = {str(r.get("keyword") or "").casefold(): r for r in base_rows if str(r.get("keyword") or "").strip()}

    to_add = 0
    to_update = 0
    for entry in payload.entries:
        keyword = _clean_field(entry.keyword)
        if not keyword:
            continue
        key = keyword.casefold()
        source = _clean_field(entry.source) or "manual"
        note = _clean_field(entry.note)
        if key not in base_map:
            to_add += 1
            base_map[key] = {"keyword": keyword, "source": source, "note": note}
            continue
        existing = base_map[key]
        if (_clean_field(existing.get("source")) != source) or (_clean_field(existing.get("note")) != note):
            to_update += 1
            existing["source"] = source
            existing["note"] = note

    return BlacklistImportPreviewResponse(
        to_add=to_add,
        to_update=to_update,
        total_after_import=len(base_map),
    )


@router.post("/blacklist/import", response_model=MessageResponse)
async def import_blacklist_keywords(
    payload: BlacklistImportRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    _ensure_config_manager()
    base = [] if payload.replace else _load_blacklist()
    rows = list(base)
    for entry in payload.entries:
        keyword = _clean_field(entry.keyword)
        if not keyword:
            continue
        source = _clean_field(entry.source) or "manual"
        note = _clean_field(entry.note)
        key = keyword.casefold()
        matched = False
        for row in rows:
            if row.get("keyword", "").casefold() == key:
                row["source"] = source
                row["note"] = note
                matched = True
                break
        if not matched:
            rows.append({"keyword": keyword, "source": source, "note": note})
    _save_blacklist(rows)
    return MessageResponse(success=True, message="blacklist_imported")


@router.post("/blacklist", response_model=MessageResponse)
async def add_blacklist_keyword(
    payload: BlacklistAddRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    _ensure_config_manager()
    kw = _clean_field(payload.keyword)
    if not kw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_keyword")

    rows = _load_blacklist()
    target_key = kw.casefold()
    source = _clean_field(payload.source) or "manual"
    note = _clean_field(payload.note)
    updated = False
    for row in rows:
        if row.get("keyword", "").casefold() == target_key:
            row["source"] = source
            row["note"] = note
            updated = True
            break
    if not updated:
        rows.append({"keyword": kw, "source": source, "note": note})

    _save_blacklist(rows)
    return MessageResponse(success=True, message="keyword_added")


@router.delete("/blacklist/{keyword}", response_model=MessageResponse)
async def remove_blacklist_keyword(
    keyword: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    _ensure_config_manager()
    kw = _clean_field(keyword)
    rows = [row for row in _load_blacklist() if row.get("keyword", "").casefold() != kw.casefold()]
    _save_blacklist(rows)
    return MessageResponse(success=True, message="keyword_removed")


@router.post("/ad-screen", response_model=AdScreenResponse)
async def ad_screen(
    payload: AdScreenRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> AdScreenResponse:
    _ensure_config_manager()
    blacklist_rows = _load_blacklist()
    blacklist_keywords = _blacklist_keywords_only(blacklist_rows)
    items: list[AdScreenItem] = []
    rule_hit_count = 0
    for row in payload.messages:
        hit, reasons = _rule_screen(str(row.text or ""), blacklist_keywords)
        if hit:
            rule_hit_count += 1
        items.append(
            AdScreenItem(
                message_id=row.message_id,
                text=row.text,
                chat_id=row.chat_id,
                has_media=bool(getattr(row, "has_media", False)),
                rule_hit=hit,
                rule_reasons=reasons,
                state="rule_screened" if hit else "needs_ai",
                suggested_action="block" if hit else "needs_ai",
                confidence=0.96 if hit else 0.35,
            )
        )

    return AdScreenResponse(
        items=items,
        summary=AdScreenSummary(total=len(items), rule_hit=rule_hit_count, needs_ai=max(0, len(items) - rule_hit_count)),
    )


@router.post("/ad-analyze", response_model=AdAnalyzeResponse)
async def ad_analyze(
    payload: AdAnalyzeRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> AdAnalyzeResponse:
    _ensure_config_manager()
    manager = _ensure_config_manager()
    config = manager.get_config()
    rows = [_ai_second_pass(item, config=config) for item in payload.items]
    block = len([x for x in rows if x.suggested_action == "block"])
    review = len([x for x in rows if x.suggested_action == "review"])
    allow = len([x for x in rows if x.suggested_action == "allow"])
    return AdAnalyzeResponse(
        items=rows,
        summary=AdAnalyzeSummary(total=len(rows), block=block, review=review, allow=allow),
    )


@router.post("/ad-decisions", response_model=AdDecisionCreateResponse)
async def create_ad_decision(
    payload: AdDecisionCreateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> AdDecisionCreateResponse:
    _ensure_config_manager()
    item = AdDecisionItem(
        decision_id=f"dec_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        created_at=int(time.time()),
        message_id=_clean_field(payload.message_id),
        text=str(payload.text or ""),
        action=_clean_field(payload.action) or "review",
        state=_clean_field(payload.state) or "review",
        reason=_clean_field(payload.reason) or "",
        chat_id=_clean_field(payload.chat_id) or None,
        source=_clean_field(payload.source) or "ui",
    )
    _append_decision(item)
    return AdDecisionCreateResponse(success=True, item=item)


@router.get("/ad-decisions", response_model=AdDecisionListResponse)
async def list_ad_decisions(
    limit: int = 50,
    action: str | None = None,
    since_ts: int | None = None,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> AdDecisionListResponse:
    _ensure_config_manager()
    rows = _list_decisions(limit=limit, action=action, since_ts=since_ts)
    return AdDecisionListResponse(count=len(rows), items=[AdDecisionItem(**r) for r in rows])


@router.get("/rule-candidates", response_model=RuleCandidateListResponse)
async def list_rule_candidates(
    limit: int = 50,
    status: str | None = None,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> RuleCandidateListResponse:
    _ensure_config_manager()
    rows = _list_rule_candidates(limit=limit, status=status)
    return RuleCandidateListResponse(count=len(rows), items=[RuleCandidateItem(**r) for r in rows])


@router.post("/rule-candidates/{candidate_id}/approve", response_model=MessageResponse)
async def approve_rule_candidate(
    candidate_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    _ensure_config_manager()
    target = _clean_field(candidate_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="candidate_id 不能为空")
    row = _update_rule_candidate_status(candidate_id=target, status_value="approved")
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="candidate not found")
    approve_result = _approve_rule_candidate_to_blacklist(row)
    if approve_result == "blacklist_added":
        return MessageResponse(success=True, message="候选规则已批准并写入屏蔽词")
    if approve_result == "blacklist_already_exists":
        return MessageResponse(success=True, message="候选规则已批准，屏蔽词已存在")
    if approve_result == "candidate_type_not_supported":
        return MessageResponse(success=True, message="候选规则已批准，但该类型暂不写入屏蔽词")
    return MessageResponse(success=True, message="候选规则已批准，但候选值为空，未写入屏蔽词")


@router.post("/rule-candidates/{candidate_id}/reject", response_model=MessageResponse)
async def reject_rule_candidate(
    candidate_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    _ensure_config_manager()
    target = _clean_field(candidate_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="candidate_id 不能为空")
    row = _update_rule_candidate_status(candidate_id=target, status_value="rejected")
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="candidate not found")
    return MessageResponse(success=True, message="候选规则已拒绝")
