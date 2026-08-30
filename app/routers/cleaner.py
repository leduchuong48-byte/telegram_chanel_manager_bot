"""Cleaner API routes for message purge."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import RPCError

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.runtime_settings import load_runtime_settings
from app.core.telethon_runtime import (
    discover_dialog_targets,
    get_bot_config,
    get_target_chat_tokens,
    map_telethon_exception,
    open_web_client,
    parse_target_chat_token,
)
from tg_media_dedupe_bot.telethon_scan import _resolve_entity as _resolve_entity_telethon
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime
from tg_media_dedupe_bot.task_models import JobSpec, JobType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cleaner", tags=["cleaner"])

_config_manager: ConfigManager | None = None
_pipeline_runtime: PipelineRuntime | None = None


class BatchDeleteRequest(BaseModel):
    count: int = Field(..., ge=1, le=5000)
    target: str | None = None


class DeleteByTypeRequest(BaseModel):
    types: list[str] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=5000)
    target: str | None = None


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def set_pipeline_runtime(runtime: PipelineRuntime | None) -> None:
    """Set the shared pipeline runtime instance."""
    global _pipeline_runtime
    _pipeline_runtime = runtime


def _get_pipeline_runtime() -> PipelineRuntime:
    if _pipeline_runtime is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pipeline runtime not initialized",
        )
    return _pipeline_runtime


def _get_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _get_target_tokens(bot_config: dict[str, Any]) -> list[str]:
    return get_target_chat_tokens(bot_config)


async def _resolve_target_entities(client: TelegramClient, tokens: list[str]) -> list[tuple[str, Any]]:
    entities: list[tuple[str, Any]] = []
    for token in tokens:
        chat, bot_chat_id = parse_target_chat_token(token)
        try:
            entity = await _resolve_entity_telethon(
                client,
                chat=chat,
                bot_chat_id=bot_chat_id,
                bot_chat_username=None,
                allow_dialog_lookup=True,
            )
        except Exception as exc:
            logger.warning("resolve_target_failed target=%s error=%s", token, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法解析目标群组/频道：{token}",
            ) from exc
        entities.append((token, entity))
    return entities


def _resolve_target_from_payload(payload: Any) -> str | None:
    if payload is None:
        return None
    raw = getattr(payload, "target", None)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


async def _resolve_targets(
    client: TelegramClient,
    bot_config: dict[str, Any],
    *,
    requested_target: str | None = None,
) -> list[tuple[str, Any]]:
    if requested_target:
        return await _resolve_target_entities(client, [requested_target])

    tokens = _get_target_tokens(bot_config)
    if tokens:
        return await _resolve_target_entities(client, tokens)

    targets = await discover_dialog_targets(client)
    if targets:
        return targets

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "未配置 target_chat_ids，且自动发现不到可管理群组/频道；"
            "请确认 Web 登录账号已加入目标群并具备管理员权限"
        ),
    )


def _chunk_list(items: list[int], size: int = 100) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _delete_messages(
    client: TelegramClient,
    entity: Any,
    message_ids: list[int],
    *,
    runtime: PipelineRuntime | None = None,
    chat_id: int | None = None,
) -> tuple[int, int]:
    deleted = 0
    failed = 0
    for chunk in _chunk_list(message_ids, 100):
        if not chunk:
            continue
        try:
            await client.delete_messages(entity, chunk)
            deleted += len(chunk)
        except RPCError as exc:
            wait_seconds = getattr(exc, "seconds", None)
            if wait_seconds is not None:
                logger.warning("delete_messages_flood_wait seconds=%s ids=%s", wait_seconds, chunk)
                if runtime is not None and chat_id is not None:
                    runtime.pause_chat(chat_id, float(wait_seconds))
                await asyncio.sleep(max(0, int(wait_seconds)))
                try:
                    await client.delete_messages(entity, chunk)
                    deleted += len(chunk)
                    continue
                except Exception as retry_exc:
                    failed += len(chunk)
                    logger.warning("delete_messages_retry_failed error=%s ids=%s", retry_exc, chunk)
                    continue
            failed += len(chunk)
            logger.warning("delete_messages_failed error=%s ids=%s", exc, chunk)
        except Exception as exc:
            wait_seconds = getattr(exc, "seconds", None)
            if wait_seconds is not None:
                logger.warning("delete_messages_flood_wait seconds=%s ids=%s", wait_seconds, chunk)
                if runtime is not None and chat_id is not None:
                    runtime.pause_chat(chat_id, float(wait_seconds))
                await asyncio.sleep(max(0, int(wait_seconds)))
                try:
                    await client.delete_messages(entity, chunk)
                    deleted += len(chunk)
                    continue
                except Exception as retry_exc:
                    failed += len(chunk)
                    logger.warning("delete_messages_retry_failed error=%s ids=%s", retry_exc, chunk)
                    continue
            failed += len(chunk)
            logger.warning("delete_messages_failed error=%s ids=%s", exc, chunk)
    return deleted, failed


def _message_matches(msg: Any, types: set[str]) -> bool:
    if "photo" in types and getattr(msg, "photo", None) is not None:
        return True
    if "video" in types and getattr(msg, "video", None) is not None:
        return True
    if "sticker" in types and getattr(msg, "sticker", None) is not None:
        return True
    if "text" in types:
        if getattr(msg, "media", None) is None and getattr(msg, "message", None):
            return True
    return False


async def _submit_cleaner_job(*, job_type: JobType, chat_target: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    job_id = f"cleaner-{job_type.value}-{int(time.time())}-{uuid4().hex[:8]}"
    chat_id = 0
    if chat_target:
        try:
            chat_id = int(chat_target)
        except (TypeError, ValueError):
            chat_id = 0
    spec = JobSpec(
        job_id=job_id,
        chat_id=chat_id,
        job_type=job_type,
        payload={"target": chat_target, **payload},
    )
    created = await runtime.submit(spec)
    return {
        "success": True,
        "job_id": created.job_id,
        "status": created.status.value,
        "job_type": created.job_type.value,
        "target": chat_target,
    }


async def _execute_batch_delete_job(spec: JobSpec) -> None:
    config_manager = _get_config_manager()
    raw_config = config_manager.get_config()
    bot_config = get_bot_config(raw_config)
    runtime_settings = load_runtime_settings(raw_config)
    target = spec.payload.get("target") if isinstance(spec.payload, dict) else None
    count_raw = spec.payload.get("count", 0) if isinstance(spec.payload, dict) else 0
    count = int(count_raw)
    dry_run = runtime_settings.dry_run
    runtime = _get_pipeline_runtime()
    db = runtime._db
    scanned = 0
    acted = 0
    failed = 0
    async with open_web_client(
        bot_config,
        unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
        bot_session_detail="当前会话为 Bot 账号，无法执行清理",
        connect_error_detail="连接 Telethon 会话失败",
    ) as client:
        targets = await _resolve_targets(client, bot_config, requested_target=target)
        for _token, entity in targets:
            message_ids: list[int] = []
            async for msg in client.iter_messages(entity, limit=count):
                if runtime.is_cancelled(spec.job_id):
                    break
                msg_id = getattr(msg, "id", None)
                if isinstance(msg_id, int) and msg_id > 0:
                    message_ids.append(msg_id)
                    scanned += 1
            if runtime.is_cancelled(spec.job_id):
                if not dry_run:
                    deleted_now, failed_now = await _delete_messages(
                        client,
                        entity,
                        message_ids,
                        runtime=runtime,
                        chat_id=spec.chat_id,
                    )
                    acted += deleted_now
                    failed += failed_now
                db.update_job_progress(spec.job_id, scanned=scanned, matched=scanned, acted=acted, failed=failed)
                return
            if dry_run:
                db.update_job_progress(spec.job_id, scanned=scanned, matched=scanned, acted=acted, failed=failed)
                continue
            deleted_now, failed_now = await _delete_messages(
                client,
                entity,
                message_ids,
                runtime=runtime,
                chat_id=spec.chat_id,
            )
            acted += deleted_now
            failed += failed_now
    db.update_job_progress(spec.job_id, scanned=scanned, matched=scanned, acted=acted, failed=failed)


async def _execute_delete_by_type_job(spec: JobSpec) -> None:
    config_manager = _get_config_manager()
    raw_config = config_manager.get_config()
    bot_config = get_bot_config(raw_config)
    runtime_settings = load_runtime_settings(raw_config)
    target = spec.payload.get("target") if isinstance(spec.payload, dict) else None
    limit_raw = spec.payload.get("limit", 100) if isinstance(spec.payload, dict) else 100
    limit = int(limit_raw)
    raw_types = spec.payload.get("types", []) if isinstance(spec.payload, dict) else []
    types = {str(item).strip().lower() for item in raw_types if str(item).strip()}
    dry_run = runtime_settings.dry_run
    runtime = _get_pipeline_runtime()
    db = runtime._db
    scanned = 0
    matched = 0
    acted = 0
    failed = 0
    async with open_web_client(
        bot_config,
        unauthorized_detail="未检测到 Web Telethon 用户会话，请先在账号管理中登录",
        bot_session_detail="当前会话为 Bot 账号，无法执行清理",
        connect_error_detail="连接 Telethon 会话失败",
    ) as client:
        targets = await _resolve_targets(client, bot_config, requested_target=target)
        for _token, entity in targets:
            message_ids: list[int] = []
            async for msg in client.iter_messages(entity, limit=limit):
                if runtime.is_cancelled(spec.job_id):
                    break
                msg_id = getattr(msg, "id", None)
                if not isinstance(msg_id, int) or msg_id <= 0:
                    continue
                scanned += 1
                if _message_matches(msg, types):
                    message_ids.append(msg_id)
            matched += len(message_ids)
            if runtime.is_cancelled(spec.job_id):
                if not dry_run:
                    deleted_now, failed_now = await _delete_messages(
                        client,
                        entity,
                        message_ids,
                        runtime=runtime,
                        chat_id=spec.chat_id,
                    )
                    acted += deleted_now
                    failed += failed_now
                db.update_job_progress(spec.job_id, scanned=scanned, matched=matched, acted=acted, failed=failed)
                return
            if dry_run:
                db.update_job_progress(spec.job_id, scanned=scanned, matched=matched, acted=acted, failed=failed)
                continue
            deleted_now, failed_now = await _delete_messages(
                client,
                entity,
                message_ids,
                runtime=runtime,
                chat_id=spec.chat_id,
            )
            acted += deleted_now
            failed += failed_now
    db.update_job_progress(spec.job_id, scanned=scanned, matched=matched, acted=acted, failed=failed)


def register_runtime_executors(runtime: PipelineRuntime) -> None:
    runtime.register_executor(JobType.BATCH_DELETE, _execute_batch_delete_job)
    runtime.register_executor(JobType.DELETE_BY_TYPE, _execute_delete_by_type_job)


@router.post("/batch_delete")
async def batch_delete(
    payload: BatchDeleteRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a batch delete job instead of executing synchronously."""
    requested_target = _resolve_target_from_payload(payload)
    return await _submit_cleaner_job(
        job_type=JobType.BATCH_DELETE,
        chat_target=requested_target,
        payload={"count": payload.count},
    )


@router.post("/delete_by_type")
async def delete_by_type(
    payload: DeleteByTypeRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a delete-by-type job instead of executing synchronously."""
    types = {t.strip().lower() for t in payload.types if isinstance(t, str)}
    types = {t for t in types if t in {"text", "photo", "video", "sticker"}}
    if not types:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择需要清理的类型")

    requested_target = _resolve_target_from_payload(payload)
    return await _submit_cleaner_job(
        job_type=JobType.DELETE_BY_TYPE,
        chat_target=requested_target,
        payload={"types": sorted(types), "limit": payload.limit},
    )





def _infer_failure_category(error_text: str | None) -> str | None:
    if not error_text:
        return None
    lowered = str(error_text).lower()
    if any(k in lowered for k in ["resolve", "无法解析", "entity", "chat"]):
        return "chat_unresolved"
    if any(k in lowered for k in ["unauthorized", "session", "登录", "会话"]):
        return "session_unavailable"
    if any(k in lowered for k in ["permission", "forbidden", "权限"]):
        return "permission_denied"
    if any(k in lowered for k in ["dry_run", "delete_disabled", "mode"]):
        return "runtime_mode_blocked"
    return "task_runtime_error"


def _recommended_action_for(category: str | None) -> str | None:
    mapping = {
        "chat_unresolved": "请先确认目标 chat 可见并可解析（会话账号需在群内）。",
        "session_unavailable": "请在执行会话页面重新登录并验证 Telethon 会话。",
        "permission_denied": "请检查 owner/admin 权限与群内管理员权限。",
        "runtime_mode_blocked": "请检查当前运行模式（dry-run/delete）是否允许该操作。",
        "task_runtime_error": "请查看任务详情日志与系统日志定位运行时异常。",
    }
    if category is None:
        return None
    return mapping.get(category)


def _infer_stage(status: str, *, started_at: int, finished_at: int) -> str:
    if status == "pending":
        return "queued"
    if status == "retry_wait":
        return "retry_wait"
    if status == "running":
        return "executing"
    if status == "completed":
        return "finished"
    if status == "failed":
        return "failed"
    if status == "failed_permanent":
        return "failed_permanent"
    if status == "dead_letter":
        return "dead_letter"
    if status == "cancelled":
        return "cancelled"
    if started_at > 0 and finished_at == 0:
        return "executing"
    return "queued"


def _derive_outcome(*, status: str, scanned: int, matched: int, acted: int, failed: int) -> tuple[str, str]:
    if status == "completed":
        if scanned > 0 and matched == 0 and acted == 0:
            return ("no_matches", f"任务已执行，但最近 {scanned} 条中没有命中纯文本消息。")
        if acted > 0:
            return ("actions_applied", f"任务已执行，已处理 {acted} 条消息。")
        return ("completed", "任务已执行并完成。")
    if status == "retry_wait":
        return ("retry_wait", "任务出现临时错误，正在等待自动重试。")
    if status == "failed":
        return ("failed", "任务执行失败，请查看失败分类与关联诊断。")
    if status == "failed_permanent":
        return ("failed_permanent", "任务发生不可重试错误，需要人工处理。")
    if status == "dead_letter":
        return ("dead_letter", "任务重试已耗尽，已进入死信队列。")
    if status == "running":
        return ("running", "任务正在执行中。")
    if status == "pending":
        return ("pending", "任务已入队，等待执行。")
    if status == "cancelled":
        return ("cancelled", "任务已取消。")
    return ("unknown", "任务状态未知。")


def _did_start(*, status: str, started_at: int) -> bool:
    if int(started_at) > 0:
        return True
    return status in {"running", "retry_wait", "completed", "failed", "failed_permanent", "dead_letter", "cancelled"}


def _did_finish(*, status: str, finished_at: int) -> bool:
    if int(finished_at) > 0:
        return True
    return status in {"completed", "failed", "failed_permanent", "dead_letter", "cancelled"}


def _business_result(*, status: str, scanned: int, matched: int, acted: int, failed: int) -> str:
    if status == "completed" and acted > 0:
        return "work_applied"
    if status == "completed" and scanned > 0 and matched == 0 and acted == 0:
        return "no_op_no_match"
    if status == "completed" and scanned == 0 and acted == 0:
        return "no_op_empty_scan"
    if status == "retry_wait":
        return "waiting_retry"
    if status in {"failed", "failed_permanent", "dead_letter"}:
        return "failed"
    if status == "running":
        if scanned > 0 or matched > 0 or acted > 0 or failed > 0:
            return "running_with_progress"
        return "running_no_progress"
    if status == "pending":
        return "queued"
    if status == "cancelled":
        if acted > 0:
            return "cancelled_partial_work"
        return "cancelled"
    return "unknown"


def _next_action_summary(*, status: str, did_work: bool, failure_category: str | None, business_result: str) -> str:
    if status == "completed" and did_work:
        return "无需处理，可按需抽查任务详情。"
    if business_result == "no_op_no_match":
        return "建议检查筛选类型或扩大扫描范围后重试。"
    if status == "pending":
        return "等待调度；若长时间未开始，请检查会话与队列状态。"
    if status == "running":
        return "等待任务结束；若长时间无进展，请查看事件历史。"
    if status == "retry_wait":
        return "系统将自动重试；若持续重试，请检查会话和群权限。"
    if status in {"failed", "failed_permanent", "dead_letter"}:
        recommended = _recommended_action_for(failure_category)
        if recommended:
            return recommended
        return "请查看错误与事件证据，完成人工复核后再处理。"
    if status == "cancelled":
        return "任务已取消；如需继续请重新提交任务。"
    return "请查看任务详情并结合事件证据判断下一步。"


def _operator_summary(
    *,
    status: str,
    scanned: int,
    matched: int,
    acted: int,
    failed: int,
    did_start: bool,
    did_finish: bool,
    business_result: str,
) -> str:
    if status == "completed" and acted > 0:
        return f"任务已完成，已实际处理 {acted} 条消息。"
    if business_result == "no_op_no_match":
        return f"任务已执行完成，但扫描 {scanned} 条后未命中目标消息。"
    if status == "pending" and not did_start:
        return "任务已入队，尚未开始执行。"
    if status == "running" and not did_finish:
        return f"任务正在执行，当前扫描 {scanned} 条，已处理 {acted} 条。"
    if status == "retry_wait":
        return "任务出现临时错误，正在等待自动重试。"
    if status == "failed":
        return f"任务执行失败，已处理 {acted} 条，失败 {failed} 条。"
    if status == "failed_permanent":
        return "任务遇到不可重试错误，需要人工处理。"
    if status == "dead_letter":
        return "任务自动重试已耗尽，已进入死信队列。"
    if status == "cancelled":
        return "任务已取消。"
    return "任务状态已记录，请查看详情确认执行结果。"


def _timing_payload(*, created_at: int, started_at: int, finished_at: int) -> dict[str, int]:
    duration = 0
    if int(started_at) > 0 and int(finished_at) >= int(started_at):
        duration = int(finished_at) - int(started_at)
    return {
        "created_at": int(created_at),
        "started_at": int(started_at),
        "finished_at": int(finished_at),
        "duration_seconds": int(duration),
    }


def _status_label(status: str) -> str:
    return {
        "pending": "等待执行",
        "running": "执行中",
        "retry_wait": "等待重试",
        "completed": "已完成",
        "failed": "执行失败",
        "failed_permanent": "永久失败",
        "dead_letter": "死信待处理",
        "cancelled": "已取消",
    }.get(status, status)


def _business_result_label(result: str) -> str:
    return {
        "work_applied": "已处理",
        "no_op_no_match": "无命中",
        "no_op_empty_scan": "无扫描结果",
        "waiting_retry": "等待重试",
        "failed": "失败",
        "running_with_progress": "执行中（有进展）",
        "running_no_progress": "执行中（暂无进展）",
        "queued": "排队中",
        "cancelled_partial_work": "已取消（部分处理）",
        "cancelled": "已取消",
        "unknown": "未知",
    }.get(result, result)


def _default_action_for_job(*, job_id: str, status: str) -> tuple[str, str]:
    if status in {"failed", "failed_permanent", "dead_letter", "retry_wait"}:
        return ("查看任务证据", f"/task_center?task={job_id}")
    return ("查看任务详情", f"/task_center?task={job_id}")


def _related_links_for_job(*, job_id: str, chat_id: int) -> dict[str, str]:
    return {
        "task_center": f"/task_center?task={job_id}",
        "session": "/session",
        "chat_visibility": f"/chat_visibility?chat_id={chat_id}",
        "logs": f"/logs?task_id={job_id}",
    }


def _build_timeline(*, created_at: int, started_at: int, finished_at: int, status: str) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = [
        {"key": "created", "label": "任务已创建", "at": int(created_at), "done": int(created_at) > 0},
        {"key": "started", "label": "开始执行", "at": int(started_at), "done": int(started_at) > 0},
        {"key": "completed", "label": "执行完成", "at": int(finished_at), "done": status == "completed" and int(finished_at) > 0},
        {"key": "failed", "label": "执行失败", "at": int(finished_at), "done": status == "failed" and int(finished_at) > 0},
        {"key": "cancelled", "label": "任务取消", "at": int(finished_at), "done": status == "cancelled" and int(finished_at) > 0},
    ]
    return timeline


@router.get("/jobs/{job_id}")
async def get_cleaner_job(
    job_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    row = runtime.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    status_value = str(row["status"])
    created_at = int(row["created_at"] or 0)
    started_at = int(row["started_at"] or 0)
    finished_at = int(row["finished_at"] or 0)
    error_text = None if row["last_error"] is None else str(row["last_error"])
    failure_category = _infer_failure_category(error_text)
    scanned = int(row["scanned"] or 0)
    matched = int(row["matched"] or 0)
    acted = int(row["acted"] or 0)
    failed = int(row["failed"] or 0)
    outcome_category, outcome_summary = _derive_outcome(
        status=status_value,
        scanned=scanned,
        matched=matched,
        acted=acted,
        failed=failed,
    )
    did_start = _did_start(status=status_value, started_at=started_at)
    did_finish = _did_finish(status=status_value, finished_at=finished_at)
    did_work = acted > 0
    business_result = _business_result(
        status=status_value,
        scanned=scanned,
        matched=matched,
        acted=acted,
        failed=failed,
    )
    operator_summary = _operator_summary(
        status=status_value,
        scanned=scanned,
        matched=matched,
        acted=acted,
        failed=failed,
        did_start=did_start,
        did_finish=did_finish,
        business_result=business_result,
    )
    next_action_summary = _next_action_summary(
        status=status_value,
        did_work=did_work,
        failure_category=failure_category,
        business_result=business_result,
    )

    return {
        "job_id": str(row["job_id"]),
        "status": status_value,
        "status_label": _status_label(status_value),
        "task_type": str(row["task_type"]),
        "chat_id": int(row["chat_id"]),
        "priority": int(row["priority"]),
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "last_error": error_text,
        "stage": _infer_stage(status_value, started_at=started_at, finished_at=finished_at),
        "failure_category": failure_category,
        "recommended_action": _recommended_action_for(failure_category),
        "timeline": _build_timeline(
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            status=status_value,
        ),
        "related_links": _related_links_for_job(job_id=str(row["job_id"]), chat_id=int(row["chat_id"])),
        "outcome_category": outcome_category,
        "outcome_summary": outcome_summary,
        "did_start": did_start,
        "did_finish": did_finish,
        "did_work": did_work,
        "business_result": business_result,
        "business_result_label": _business_result_label(business_result),
        "operator_summary": operator_summary,
        "next_action_summary": next_action_summary,
        "timing": _timing_payload(created_at=created_at, started_at=started_at, finished_at=finished_at),
        "progress": {
            "scanned": scanned,
            "matched": matched,
            "acted": acted,
            "failed": failed,
        },
    }


@router.get("/jobs/{job_id}/events")
async def get_cleaner_job_events(
    job_id: str,
    limit: int = 200,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    row = runtime.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    events = []
    for item in runtime._db.list_job_events(job_id, limit=limit):
        payload_json = str(item["event_payload_json"] or "{}")
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {}
        events.append(
            {
                "id": int(item["id"]),
                "event_type": str(item["event_type"]),
                "payload": payload,
                "created_at": int(item["created_at"]),
            }
        )

    return {
        "job_id": job_id,
        "count": len(events),
        "events": events,
    }



@router.post("/jobs/{job_id}/cancel")
async def cancel_cleaner_job(
    job_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    cancelled = await runtime.cancel(job_id)
    row = runtime.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "success": cancelled or str(row["status"]) == "cancelled",
        "job_id": str(row["job_id"]),
        "status": str(row["status"]),
    }



@router.get("/jobs")
async def list_cleaner_jobs(
    limit: int = 50,
    status: str | None = None,
    task_type: str | None = None,
    chat_id: int | None = None,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    rows = runtime._db.list_jobs(limit=limit, status=status, task_type=task_type, chat_id=chat_id)
    jobs = []
    for row in rows:
        status_value = str(row["status"])
        scanned = int(row["scanned"] or 0)
        matched = int(row["matched"] or 0)
        acted = int(row["acted"] or 0)
        failed = int(row["failed"] or 0)
        outcome_category, outcome_summary = _derive_outcome(
            status=status_value,
            scanned=scanned,
            matched=matched,
            acted=acted,
            failed=failed,
        )
        started_at = int(row["started_at"] or 0)
        finished_at = int(row["finished_at"] or 0)
        failure_category = _infer_failure_category(None if row["last_error"] is None else str(row["last_error"]))
        did_start = _did_start(status=status_value, started_at=started_at)
        did_finish = _did_finish(status=status_value, finished_at=finished_at)
        did_work = acted > 0
        business_result = _business_result(
            status=status_value,
            scanned=scanned,
            matched=matched,
            acted=acted,
            failed=failed,
        )
        jobs.append(
            {
                "job_id": str(row["job_id"]),
                "status": status_value,
                "status_label": _status_label(status_value),
                "task_type": str(row["task_type"]),
                "chat_id": int(row["chat_id"]),
                "priority": int(row["priority"]),
                "created_at": int(row["created_at"]),
                "started_at": started_at,
                "finished_at": finished_at,
                "last_error": None if row["last_error"] is None else str(row["last_error"]),
                "stage": _infer_stage(status_value, started_at=started_at, finished_at=finished_at),
                "failure_category": failure_category,
                "outcome_category": outcome_category,
                "outcome_summary": outcome_summary,
                "did_start": did_start,
                "did_finish": did_finish,
                "did_work": did_work,
                "business_result": business_result,
                "business_result_label": _business_result_label(business_result),
                "operator_summary": _operator_summary(
                    status=status_value,
                    scanned=scanned,
                    matched=matched,
                    acted=acted,
                    failed=failed,
                    did_start=did_start,
                    did_finish=did_finish,
                    business_result=business_result,
                ),
                "next_action_summary": _next_action_summary(
                    status=status_value,
                    did_work=did_work,
                    failure_category=failure_category,
                    business_result=business_result,
                ),
                "timing": _timing_payload(
                    created_at=int(row["created_at"]),
                    started_at=started_at,
                    finished_at=finished_at,
                ),
                "action_label": _default_action_for_job(job_id=str(row["job_id"]), status=status_value)[0],
                "action_href": _default_action_for_job(job_id=str(row["job_id"]), status=status_value)[1],
                "progress": {
                    "scanned": scanned,
                    "matched": matched,
                    "acted": acted,
                    "failed": failed,
                },
            }
        )
    return {"jobs": jobs}



@router.get("/monitoring")
async def get_cleaner_monitoring(
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, int]:
    runtime = _get_pipeline_runtime()
    return runtime.monitoring_summary()


@router.get("/review_queue")
async def list_review_queue_jobs(
    limit: int = 50,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    rows = runtime._db.list_jobs(limit=max(200, limit * 5))
    items: list[dict[str, Any]] = []

    for row in rows:
        status_value = str(row["status"])
        scanned = int(row["scanned"] or 0)
        matched = int(row["matched"] or 0)
        acted = int(row["acted"] or 0)
        attempt_count = int(row["attempt_count"] or 0)
        max_attempts = int(row["max_attempts"] or 0)
        reason = ""
        suggestion = ""

        if status_value in {"dead_letter", "failed_permanent"}:
            reason = "permanent_failure"
            suggestion = "进入死信/永久失败，建议人工复核后再重放。"
        elif status_value == "failed":
            reason = "runtime_failure"
            suggestion = "任务失败，先看错误与事件历史，再决定重试。"
        elif status_value == "retry_wait" and attempt_count >= 2:
            reason = "unstable_retry"
            suggestion = "任务多次重试，建议检查目标群权限与会话状态。"
        elif status_value == "completed" and scanned > 0 and matched == 0 and acted == 0:
            reason = "no_match_no_action"
            suggestion = "执行完成但无命中，建议复核清洗条件或目标范围。"

        if not reason:
            continue

        job_id = str(row["job_id"])
        chat_id = int(row["chat_id"])
        items.append(
            {
                "job_id": job_id,
                "status": status_value,
                "status_label": _status_label(status_value),
                "task_type": str(row["task_type"]),
                "chat_id": chat_id,
                "created_at": int(row["created_at"] or 0),
                "finished_at": int(row["finished_at"] or 0),
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "reason": reason,
                "suggestion": suggestion,
                "next_action_summary": suggestion,
                "last_error": None if row["last_error"] is None else str(row["last_error"]),
                "outcome_summary": _derive_outcome(
                    status=status_value,
                    scanned=scanned,
                    matched=matched,
                    acted=acted,
                    failed=int(row["failed"] or 0),
                )[1],
                "business_result": _business_result(
                    status=status_value,
                    scanned=scanned,
                    matched=matched,
                    acted=acted,
                    failed=int(row["failed"] or 0),
                ),
                "business_result_label": _business_result_label(
                    _business_result(
                        status=status_value,
                        scanned=scanned,
                        matched=matched,
                        acted=acted,
                        failed=int(row["failed"] or 0),
                    )
                ),
                "links": {
                    "task_center": f"/task_center?task={job_id}",
                    "chat_visibility": f"/chat_visibility?chat_id={chat_id}",
                    "logs": f"/logs?task_id={job_id}",
                },
                "action_label": "查看任务证据",
                "action_href": f"/task_center?task={job_id}",
            }
        )

    items.sort(key=lambda item: (int(item["finished_at"] or 0), int(item["created_at"] or 0)), reverse=True)
    return {"count": min(len(items), limit), "items": items[: max(1, min(limit, 200))]}


@router.get("/dead_letters")
async def list_dead_letter_jobs(
    limit: int = 50,
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    rows = runtime._db.list_dead_letter_jobs(limit=limit)
    items = []
    for row in rows:
        retryable_class = str(row["retryable_class"] or "")
        next_action_summary = "请先查看任务详情与事件证据，再决定是否重放。"
        if retryable_class == "permission_denied":
            next_action_summary = "请先修复管理员权限，再重试。"
        elif retryable_class == "session_unavailable":
            next_action_summary = "请在执行会话页面重新登录后再重试。"

        items.append(
            {
                "job_id": str(row["job_id"]),
                "status": str(row["status"]),
                "status_label": _status_label(str(row["status"])),
                "task_type": str(row["task_type"]),
                "chat_id": int(row["chat_id"]),
                "attempt_count": int(row["attempt_count"] or 0),
                "max_attempts": int(row["max_attempts"] or 0),
                "terminal_reason": str(row["terminal_reason"] or ""),
                "retryable_class": retryable_class,
                "last_error": None if row["last_error"] is None else str(row["last_error"]),
                "finished_at": int(row["finished_at"] or 0),
                "next_action_summary": next_action_summary,
                "action_label": "查看任务证据",
                "action_href": f"/task_center?task={str(row['job_id'])}",
            }
        )
    return {"count": len(items), "items": items}


@router.get("/locks")
async def list_target_locks(
    _: Annotated[str, Depends(get_current_user)] = "",
) -> dict[str, Any]:
    runtime = _get_pipeline_runtime()
    rows = runtime._db.list_target_locks()
    items = []
    for row in rows:
        items.append(
            {
                "lock_key": str(row["lock_key"]),
                "job_id": str(row["job_id"]),
                "worker_id": str(row["worker_id"]),
                "lease_expires_at": int(row["lease_expires_at"] or 0),
                "updated_at": int(row["updated_at"] or 0),
            }
        )
    return {"count": len(items), "items": items}
