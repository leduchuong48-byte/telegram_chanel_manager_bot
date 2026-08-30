from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.reliability import classify_delete_error


logger = logging.getLogger("tg_media_dedupe_bot.forwarding")


@dataclass(frozen=True)
class ForwardingRule:
    source_id: int
    target_id: int
    mode: str
    copy: bool = False
    filter_video: bool = False

    @property
    def rule_id(self) -> str:
        return f"{self.mode}:{self.source_id}:{self.target_id}"


def parse_forwarding_rules(raw_rules: Any) -> list[ForwardingRule]:
    if not isinstance(raw_rules, list):
        return []
    result: list[ForwardingRule] = []
    seen: set[str] = set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        mode = str(raw.get("type") or "").strip().lower()
        if mode not in {"bot_mode", "user_mode"}:
            continue
        try:
            source_id = int(raw.get("source_id"))
            target_id = int(raw.get("target_id"))
        except (TypeError, ValueError):
            continue
        if source_id == target_id:
            continue
        options = raw.get("options", {})
        if not isinstance(options, dict):
            options = {}
        rule = ForwardingRule(
            source_id=source_id,
            target_id=target_id,
            mode=mode,
            copy=bool(options.get("copy", False)),
            filter_video=bool(options.get("filter_video", False)),
        )
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        result.append(rule)
    return result


def _is_video(message: Any) -> bool:
    return any(
        getattr(message, field, None) is not None
        for field in ("video", "animation", "video_note")
    )


def _target_message_ids(result: Any) -> list[int]:
    values = result if isinstance(result, (list, tuple)) else [result]
    ids: list[int] = []
    for value in values:
        message_id = int(getattr(value, "message_id", 0) or getattr(value, "id", 0) or 0)
        if message_id > 0:
            ids.append(message_id)
    return ids


class BotForwardingEngine:
    def __init__(
        self,
        db: Database,
        rules_provider: Callable[[], list[ForwardingRule]],
        *,
        album_wait_seconds: float = 1.0,
    ) -> None:
        self._db = db
        self._rules_provider = rules_provider
        self._album_wait_seconds = max(0.0, float(album_wait_seconds))
        self._album_messages: dict[tuple[int, str], list[Any]] = {}
        self._album_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}
        self._worker_id = f"bot-forward-{uuid.uuid4().hex[:12]}"

    def _rules_for_source(self, source_id: int) -> list[ForwardingRule]:
        return [
            rule
            for rule in self._rules_provider()
            if rule.mode == "bot_mode" and int(rule.source_id) == int(source_id)
        ]

    async def handle(self, message: Any, bot: Any) -> None:
        chat = getattr(message, "chat", None)
        source_id = int(getattr(chat, "id", 0) or 0)
        if source_id == 0 or not self._rules_for_source(source_id):
            return
        group_id = str(getattr(message, "media_group_id", "") or "")
        if not group_id:
            await self._dispatch(source_id, [message], bot)
            return
        key = (source_id, group_id)
        self._album_messages.setdefault(key, []).append(message)
        if key not in self._album_tasks:
            self._album_tasks[key] = asyncio.create_task(self._flush_album(key, bot))

    async def _flush_album(self, key: tuple[int, str], bot: Any) -> None:
        try:
            await asyncio.sleep(self._album_wait_seconds)
            messages = self._album_messages.pop(key, [])
            if messages:
                await self._dispatch(key[0], messages, bot)
        finally:
            self._album_tasks.pop(key, None)

    async def _dispatch(self, source_id: int, messages: list[Any], bot: Any) -> None:
        ordered = sorted(messages, key=lambda value: int(getattr(value, "message_id", 0) or 0))
        for rule in self._rules_for_source(source_id):
            if rule.filter_video and any(_is_video(message) for message in ordered):
                continue
            selected = list(ordered)
            message_ids = [int(getattr(message, "message_id", 0) or 0) for message in selected]
            message_ids = [message_id for message_id in message_ids if message_id > 0]
            if not message_ids:
                continue
            group_id = str(getattr(selected[0], "media_group_id", "") or "")
            claimed = self._db.claim_forwarding_batch(
                rule_id=rule.rule_id,
                source_chat_id=source_id,
                target_chat_id=rule.target_id,
                source_message_ids=message_ids,
                media_group_id=group_id,
                worker_id=self._worker_id,
            )
            if not claimed:
                continue
            try:
                result = await self._send_with_retry(rule, message_ids, bot)
            except Exception as exc:  # noqa: BLE001
                failure = classify_delete_error(exc)
                record_failure = (
                    self._db.mark_forwarding_delivery_unknown
                    if failure.disposition == "retryable"
                    else self._db.fail_forwarding_batch
                )
                record_failure(
                    rule_id=rule.rule_id,
                    source_chat_id=source_id,
                    target_chat_id=rule.target_id,
                    source_message_ids=message_ids,
                    error=str(exc),
                    worker_id=self._worker_id,
                )
                logger.exception(
                    "bot_forward_failed rule=%s source_messages=%s error=%s",
                    rule.rule_id,
                    message_ids,
                    exc,
                )
                continue
            self._db.complete_forwarding_batch(
                rule_id=rule.rule_id,
                source_chat_id=source_id,
                target_chat_id=rule.target_id,
                source_message_ids=message_ids,
                target_message_ids=_target_message_ids(result),
                worker_id=self._worker_id,
            )

    async def _send_with_retry(self, rule: ForwardingRule, message_ids: list[int], bot: Any) -> Any:
        if len(message_ids) > 1:
            method = bot.copy_messages if rule.copy else bot.forward_messages
            return await method(
                chat_id=rule.target_id,
                from_chat_id=rule.source_id,
                message_ids=message_ids,
            )
        method = bot.copy_message if rule.copy else bot.forward_message
        return await method(
            chat_id=rule.target_id,
            from_chat_id=rule.source_id,
            message_id=message_ids[0],
        )

    async def shutdown(self) -> None:
        tasks = list(self._album_tasks.values())
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


class UserForwardingEngine:
    def __init__(self, db: Database, rules_provider: Callable[[], list[ForwardingRule]]) -> None:
        self._db = db
        self._rules_provider = rules_provider
        self._worker_id = f"user-forward-{uuid.uuid4().hex[:12]}"

    def _rules_for_source(self, source_id: int) -> list[ForwardingRule]:
        return [
            rule
            for rule in self._rules_provider()
            if rule.mode == "user_mode" and int(rule.source_id) == int(source_id)
        ]

    async def handle(self, source_id: int, messages: list[Any], client: Any) -> None:
        ordered = sorted(messages, key=lambda value: int(getattr(value, "id", 0) or 0))
        for rule in self._rules_for_source(source_id):
            if rule.filter_video and any(_is_video(message) for message in ordered):
                continue
            selected = list(ordered)
            group_id = str(getattr(selected[0], "grouped_id", "") or "")
            if rule.copy and len(selected) > 1 and not (
                group_id and all(getattr(message, "media", None) is not None for message in selected)
            ):
                for message in selected:
                    await self._deliver(rule, source_id, [message], client)
                continue
            await self._deliver(rule, source_id, selected, client)

    async def _deliver(
        self,
        rule: ForwardingRule,
        source_id: int,
        selected: list[Any],
        client: Any,
    ) -> None:
            message_ids = [int(getattr(message, "id", 0) or 0) for message in selected]
            message_ids = [message_id for message_id in message_ids if message_id > 0]
            if not message_ids:
                return
            group_id = str(getattr(selected[0], "grouped_id", "") or "")
            claimed = self._db.claim_forwarding_batch(
                rule_id=rule.rule_id,
                source_chat_id=source_id,
                target_chat_id=rule.target_id,
                source_message_ids=message_ids,
                media_group_id=group_id,
                worker_id=self._worker_id,
            )
            if not claimed:
                return
            try:
                result = await self._send_with_retry(rule, selected, client)
            except Exception as exc:  # noqa: BLE001
                failure = classify_delete_error(exc)
                record_failure = (
                    self._db.mark_forwarding_delivery_unknown
                    if failure.disposition == "retryable"
                    else self._db.fail_forwarding_batch
                )
                record_failure(
                    rule_id=rule.rule_id,
                    source_chat_id=source_id,
                    target_chat_id=rule.target_id,
                    source_message_ids=message_ids,
                    error=str(exc),
                    worker_id=self._worker_id,
                )
                logger.exception(
                    "user_forward_failed rule=%s source_messages=%s error=%s",
                    rule.rule_id,
                    message_ids,
                    exc,
                )
                return
            self._db.complete_forwarding_batch(
                rule_id=rule.rule_id,
                source_chat_id=source_id,
                target_chat_id=rule.target_id,
                source_message_ids=message_ids,
                target_message_ids=_target_message_ids(result),
                worker_id=self._worker_id,
            )

    async def _send_with_retry(self, rule: ForwardingRule, messages: list[Any], client: Any) -> Any:
        if not rule.copy:
            return await client.forward_messages(
                rule.target_id,
                [int(getattr(message, "id", 0)) for message in messages],
                from_peer=rule.source_id,
            )
        if len(messages) > 1 and all(getattr(message, "media", None) is not None for message in messages):
            return await client.send_file(
                rule.target_id,
                [message.media for message in messages],
                caption=[str(getattr(message, "message", "") or "") for message in messages],
            )
        message = messages[0]
        media = getattr(message, "media", None)
        text = str(getattr(message, "message", "") or "")
        if media is not None:
            return await client.send_file(rule.target_id, media, caption=text)
        return await client.send_message(
            rule.target_id,
            text,
            formatting_entities=getattr(message, "entities", None),
        )
