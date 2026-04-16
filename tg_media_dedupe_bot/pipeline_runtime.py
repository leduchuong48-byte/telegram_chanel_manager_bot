from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.task_models import JobSpec, JobStatus, JobType


Executor = Callable[[JobSpec], Awaitable[None]]


def _format_exception_text(exc: Exception) -> str:
    raw_text = str(exc).strip()
    if raw_text and raw_text != exc.__class__.__name__:
        return raw_text

    detail = getattr(exc, "detail", None)
    if detail is not None:
        if isinstance(detail, str):
            detail_text = detail.strip()
            if detail_text:
                return detail_text
        else:
            detail_text = str(detail).strip()
            if detail_text:
                return detail_text

    return raw_text or exc.__class__.__name__


def _classify_exception(exc: Exception) -> tuple[bool, str, str]:
    text = _format_exception_text(exc)
    lowered = text.lower()

    if isinstance(exc, PermissionError) or any(k in lowered for k in ["permission", "forbidden", "权限"]):
        return (False, "permission_denied", text)
    if any(k in lowered for k in ["unable to resolve", "无法解析", "resolve", "entity", "chat"]):
        return (False, "chat_unresolved", text)
    if any(k in lowered for k in ["unauthorized", "session", "登录", "会话"]):
        return (False, "session_unavailable", text)

    if isinstance(exc, TimeoutError):
        return (True, "timeout", text)
    if isinstance(exc, OSError):
        return (True, "io_transient", text)

    return (False, "task_runtime_error", text)


class PipelineRuntime:
    def __init__(self, db: Database, worker_count: int = 1) -> None:
        self._db = db
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._started = False
        self._workers: list[asyncio.Task[None]] = []
        self._executors: dict[JobType, Executor] = {}
        self._worker_count = max(1, int(worker_count))
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._chat_paused_until: dict[int, float] = {}
        self._worker_id = f"worker-{uuid.uuid4().hex[:8]}"

    @property
    def is_running(self) -> bool:
        return self._started and any(not worker.done() for worker in self._workers)

    def register_executor(self, job_type: JobType, executor: Executor) -> None:
        self._executors[job_type] = executor

    async def submit(self, spec: JobSpec) -> JobSpec:
        self._db.create_job(
            job_id=spec.job_id,
            chat_id=spec.chat_id,
            task_type=spec.job_type.value,
            payload_json=json.dumps(spec.payload, ensure_ascii=False, sort_keys=True),
            priority=spec.priority,
        )
        self._db.append_job_event(job_id=spec.job_id, event_type="created")
        if self._started:
            await self._queue.put(spec.job_id)
        return spec

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        now = int(time.time())
        rows = self._db._conn.execute(
            """
            SELECT job_id
            FROM jobs
            WHERE status IN ('pending', 'running', 'retry_wait')
              AND (next_run_at IS NULL OR next_run_at = 0 OR next_run_at <= ?)
            ORDER BY created_at ASC
            """,
            (now,),
        ).fetchall()
        for row in rows:
            await self._queue.put(str(row["job_id"]))
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"pipeline-worker-{i}")
            for i in range(self._worker_count)
        ]

    async def _worker_loop(self) -> None:
        while self._started:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._process_job(job_id)
            finally:
                self._queue.task_done()

    async def drain_once(self) -> None:
        if self._queue.empty():
            return
        job_id = await self._queue.get()
        try:
            await self._process_job(job_id)
        finally:
            self._queue.task_done()

    async def shutdown(self) -> None:
        self._started = False
        workers = list(self._workers)
        self._workers.clear()
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def _acquire_target_lock(self, *, job_id: str, chat_id: int) -> str:
        lock_key = f"chat:{int(chat_id)}"
        waiting_noted = False
        while True:
            acquired = self._db.acquire_target_lock(
                lock_key=lock_key,
                job_id=job_id,
                worker_id=self._worker_id,
                lease_seconds=30,
            )
            if acquired:
                if waiting_noted:
                    self._db.append_job_event(job_id=job_id, event_type="lock_acquired_after_wait")
                else:
                    self._db.append_job_event(job_id=job_id, event_type="lock_acquired")
                return lock_key
            if not waiting_noted:
                waiting_noted = True
                self._db.append_job_event(job_id=job_id, event_type="lock_wait")
            await asyncio.sleep(0.02)

    def _release_target_lock(self, *, lock_key: str, job_id: str) -> None:
        self._db.release_target_lock(lock_key=lock_key, job_id=job_id)

    async def _process_job(self, job_id: str) -> None:
        spec = self._load_job_spec(job_id)
        if spec is None:
            return

        current = self._db.get_job(job_id)
        if current is None:
            return

        status = str(current["status"])
        if status == JobStatus.CANCELLED.value:
            return

        next_run_at = int(current["next_run_at"] or 0)
        if status == JobStatus.RETRY_WAIT.value and next_run_at > int(time.time()):
            await asyncio.sleep(max(0, next_run_at - int(time.time())))

        lock = self._chat_locks.setdefault(spec.chat_id, asyncio.Lock())
        async with lock:
            current = self._db.get_job(job_id)
            if current is not None and str(current["status"]) == JobStatus.CANCELLED.value:
                return

            paused_until = self._chat_paused_until.get(spec.chat_id, 0.0)
            now_monotonic = asyncio.get_running_loop().time()
            if paused_until > now_monotonic:
                await asyncio.sleep(paused_until - now_monotonic)

            lock_key = await self._acquire_target_lock(job_id=job_id, chat_id=spec.chat_id)
            try:
                self._db.update_job_status(job_id, JobStatus.RUNNING.value)
                self._db.append_job_event(job_id=job_id, event_type="started")

                executor = self._executors.get(spec.job_type)
                if executor is not None:
                    await executor(spec)

                current = self._db.get_job(job_id)
                if current is not None and str(current["status"]) == JobStatus.CANCELLED.value:
                    return

                self._db.update_job_status(job_id, JobStatus.COMPLETED.value)
                self._db.append_job_event(job_id=job_id, event_type="completed")
            except Exception as exc:  # noqa: BLE001
                current = self._db.get_job(job_id)
                if current is not None and str(current["status"]) == JobStatus.CANCELLED.value:
                    return

                retryable, retry_class, error_text = _classify_exception(exc)
                current_attempt = int((current["attempt_count"] if current is not None else 0) or 0)
                max_attempts = int((current["max_attempts"] if current is not None else 3) or 3)
                next_attempt = current_attempt + 1

                if retryable and next_attempt < max_attempts:
                    delay = 1
                    next_run_at = int(time.time()) + delay
                    self._db.set_job_retry_wait(
                        job_id=job_id,
                        attempt_count=next_attempt,
                        next_run_at=next_run_at,
                        retryable_class=retry_class,
                        error=error_text,
                    )
                    self._db.append_job_event(job_id=job_id, event_type="retry_wait")
                    await asyncio.sleep(delay)
                    if self._started:
                        await self._queue.put(job_id)
                    return

                if retryable and next_attempt >= max_attempts:
                    self._db.mark_job_dead_letter(
                        job_id=job_id,
                        attempt_count=next_attempt,
                        retryable_class=retry_class,
                        terminal_reason="retry_exhausted",
                        error=error_text,
                    )
                    self._db.append_job_event(job_id=job_id, event_type="dead_letter")
                    return

                # non-retryable path (keep backward-compatible failed status)
                self._db.update_job_status(job_id, JobStatus.FAILED.value, error=error_text)
                with self._db._conn:
                    self._db._conn.execute(
                        """
                        UPDATE jobs
                        SET attempt_count=?, retryable_class=?, terminal_reason=?
                        WHERE job_id=?
                        """,
                        (next_attempt, retry_class, retry_class, job_id),
                    )
                self._db.append_job_event(job_id=job_id, event_type="failed")
            finally:
                self._release_target_lock(lock_key=lock_key, job_id=job_id)

    def _load_job_spec(self, job_id: str) -> JobSpec | None:
        row = self._db.get_job(job_id)
        if row is None:
            return None
        return JobSpec(
            job_id=str(row["job_id"]),
            chat_id=int(row["chat_id"]),
            job_type=JobType(str(row["task_type"])),
            payload=json.loads(str(row["payload_json"])),
            status=JobStatus(str(row["status"])),
            priority=int(row["priority"]),
        )

    async def cancel(self, job_id: str) -> bool:
        row = self._db.get_job(job_id)
        if row is None:
            return False
        if str(row["status"]) in {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.FAILED_PERMANENT.value,
            JobStatus.DEAD_LETTER.value,
            JobStatus.CANCELLED.value,
        }:
            return False
        self._db.update_job_status(job_id, JobStatus.CANCELLED.value)
        self._db.append_job_event(job_id=job_id, event_type="cancelled")
        return True

    def is_cancelled(self, job_id: str) -> bool:
        row = self._db.get_job(job_id)
        if row is None:
            return False
        return str(row["status"]) == JobStatus.CANCELLED.value

    def pause_chat(self, chat_id: int, seconds: float) -> None:
        delay = max(0.0, float(seconds))
        self._chat_paused_until[int(chat_id)] = asyncio.get_running_loop().time() + delay

    async def mark_running(self, job_id: str) -> JobSpec:
        self._db.update_job_status(job_id, JobStatus.RUNNING.value)
        row = self._db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        return JobSpec(
            job_id=str(row["job_id"]),
            chat_id=int(row["chat_id"]),
            job_type=JobType(str(row["task_type"])),
            payload=json.loads(str(row["payload_json"])),
            status=JobStatus(str(row["status"])),
            priority=int(row["priority"]),
        )

    def monitoring_summary(self) -> dict[str, int]:
        running_jobs = len(self._db.list_jobs(limit=500, status=JobStatus.RUNNING.value))
        recent_failed_jobs = len(self._db.list_jobs(limit=500, status=JobStatus.FAILED.value)) + len(
            self._db.list_jobs(limit=500, status=JobStatus.FAILED_PERMANENT.value)
        ) + len(self._db.list_jobs(limit=500, status=JobStatus.DEAD_LETTER.value))
        now = asyncio.get_running_loop().time()
        paused_chats = sum(1 for paused_until in self._chat_paused_until.values() if paused_until > now)
        return {
            "running_jobs": running_jobs,
            "paused_chats": paused_chats,
            "recent_failed_jobs": recent_failed_jobs,
        }

    def get_job(self, job_id: str):
        return self._db.get_job(job_id)
