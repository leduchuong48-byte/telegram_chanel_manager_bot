from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.reliability import classify_delete_error


@dataclass(frozen=True)
class DeletionBatchResult:
    deleted: int = 0
    terminal: int = 0
    failed: int = 0
    skipped: int = 0


async def _call_with_retry(call) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await call()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            failure = classify_delete_error(exc)
            if failure.disposition != "retryable" or attempt >= 2:
                raise
            delay = failure.retry_after if failure.retry_after is not None else min(4, 2**attempt)
            await asyncio.sleep(max(0, delay))
    if last_error is not None:
        raise last_error
    raise RuntimeError("delete attempt did not execute")


def _record_success(db: Database, *, chat_id: int, rows: list[tuple[int, str, str]]) -> None:
    for message_id, media_key, reason in rows:
        db.record_deletion_attempt(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            result="success",
            error=None,
        )
        db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
        db.record_deletion_event(
            chat_id=chat_id,
            message_id=message_id,
            event_type="delete_succeeded",
            reason=reason,
            result="success",
            detail="transport=bot_api_batch",
        )


async def process_pending_deletions(
    *,
    db: Database,
    bot: Any,
    chat_id: int,
    pending: list[tuple[int, str, str]],
    retry_failed: bool = True,
) -> DeletionBatchResult:
    candidates: list[tuple[int, str, str]] = []
    skipped = 0
    terminal = 0
    for message_id, media_key, reason in pending:
        existing = db.get_deletion_record(chat_id, message_id)
        if existing is None:
            candidates.append((message_id, media_key, reason))
            continue
        if existing.result == "success":
            db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
            skipped += 1
            continue
        failure = classify_delete_error(RuntimeError(existing.error or ""))
        if failure.disposition == "terminal":
            db.record_deletion_failure(
                chat_id=chat_id,
                message_id=message_id,
                media_key=media_key,
                reason=reason,
                error=existing.error or failure.category,
                detail_prefix="reconciled_during_flush",
            )
            terminal += 1
            continue
        if not retry_failed:
            skipped += 1
            continue
        candidates.append((message_id, media_key, reason))

    deleted = 0
    failed = 0
    batch_method = getattr(bot, "delete_messages", None)
    for start in range(0, len(candidates), 100):
        rows = candidates[start : start + 100]
        if not rows:
            continue
        if callable(batch_method):
            try:
                await _call_with_retry(
                    lambda rows=rows: batch_method(
                        chat_id=chat_id,
                        message_ids=[message_id for message_id, _, _ in rows],
                    )
                )
            except Exception as exc:  # noqa: BLE001
                batch_failure = classify_delete_error(exc)
                if batch_failure.disposition == "terminal":
                    for message_id, media_key, reason in rows:
                        try:
                            await _call_with_retry(
                                lambda message_id=message_id: bot.delete_message(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                )
                            )
                        except Exception as single_exc:  # noqa: BLE001
                            failure = db.record_deletion_failure(
                                chat_id=chat_id,
                                message_id=message_id,
                                media_key=media_key,
                                reason=reason,
                                error=str(single_exc),
                                detail_prefix="transport=bot_api_single_fallback",
                            )
                            if failure.disposition == "terminal":
                                terminal += 1
                            else:
                                failed += 1
                            continue
                        _record_success(db, chat_id=chat_id, rows=[(message_id, media_key, reason)])
                        deleted += 1
                    continue
                for message_id, media_key, reason in rows:
                    failure = db.record_deletion_failure(
                        chat_id=chat_id,
                        message_id=message_id,
                        media_key=media_key,
                        reason=reason,
                        error=str(exc),
                        detail_prefix="transport=bot_api_batch",
                    )
                    if failure.disposition == "terminal":
                        terminal += 1
                    else:
                        failed += 1
                continue
            _record_success(db, chat_id=chat_id, rows=rows)
            deleted += len(rows)
            continue

        for message_id, media_key, reason in rows:
            try:
                await _call_with_retry(
                    lambda message_id=message_id: bot.delete_message(
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failure = db.record_deletion_failure(
                    chat_id=chat_id,
                    message_id=message_id,
                    media_key=media_key,
                    reason=reason,
                    error=str(exc),
                    detail_prefix="transport=bot_api_single_fallback",
                )
                if failure.disposition == "terminal":
                    terminal += 1
                else:
                    failed += 1
                continue
            _record_success(db, chat_id=chat_id, rows=[(message_id, media_key, reason)])
            deleted += 1

    return DeletionBatchResult(deleted=deleted, terminal=terminal, failed=failed, skipped=skipped)
