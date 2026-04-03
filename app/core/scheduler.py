"""AsyncIOScheduler wrapper for RSS fetch jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.rss_fetcher import run_full_scan

logger = logging.getLogger(__name__)

_SCHEDULER: AsyncIOScheduler | None = None
_SCHEDULER_LOCK = asyncio.Lock()


async def placeholder_task() -> None:
    """Real RSS fetch logic wrapper."""
    try:
        await run_full_scan()
    except Exception as exc:  # noqa: BLE001
        logger.error("调度任务执行失败: %s", exc, exc_info=True)


def _ensure_scheduler() -> AsyncIOScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = AsyncIOScheduler()
    return _SCHEDULER


def _parse_interval(config: dict[str, Any]) -> int:
    default = 300
    if not isinstance(config, dict):
        return default
    raw = config.get("rss_fetch_interval", default)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw if raw > 0 else default
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


async def start_scheduler(config: dict[str, Any] | None = None) -> None:
    async with _SCHEDULER_LOCK:
        scheduler = _ensure_scheduler()
        if not scheduler.running:
            scheduler.start()
        await _reload_job_locked(scheduler, config or {})


async def shutdown_scheduler() -> None:
    global _SCHEDULER
    async with _SCHEDULER_LOCK:
        if _SCHEDULER is not None and _SCHEDULER.running:
            _SCHEDULER.shutdown(wait=False)
        _SCHEDULER = None


async def reload_scheduler_job(config: dict[str, Any]) -> None:
    async with _SCHEDULER_LOCK:
        scheduler = _ensure_scheduler()
        if not scheduler.running:
            scheduler.start()
        await _reload_job_locked(scheduler, config)


async def _reload_job_locked(scheduler: AsyncIOScheduler, config: dict[str, Any]) -> None:
    job_id = "rss_fetch_job"
    if scheduler.get_job(job_id) is not None:
        scheduler.remove_job(job_id)
    interval = _parse_interval(config)
    scheduler.add_job(
        placeholder_task,
        trigger=IntervalTrigger(seconds=interval),
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    logger.info("调度器已重载，任务间隔已更新为 %s 秒。", interval)
