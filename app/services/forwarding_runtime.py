from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable

from app.core.telethon_runtime import get_api_credentials, get_bot_config, resolve_web_session_path
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.forwarding import ForwardingRule, UserForwardingEngine, parse_forwarding_rules
from tg_media_dedupe_bot.paths import resolve_app_path, telethon_session_db_path


logger = logging.getLogger(__name__)


def _default_client_factory(session: str, api_id: int, api_hash: str, **kwargs: Any):
    from telethon import TelegramClient

    return TelegramClient(session, api_id, api_hash, **kwargs)


def _clone_session_if_needed(source: Path, target: Path) -> None:
    if target.exists() or not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    target.chmod(0o600)


class UserForwardingRuntime:
    def __init__(
        self,
        db: Database | None = None,
        *,
        client_factory: Callable[..., Any] | None = None,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self._db = db
        self._client_factory = client_factory or _default_client_factory
        self._rules: list[ForwardingRule] = []
        self._engine = UserForwardingEngine(db, lambda: list(self._rules)) if db is not None else None
        self._task: asyncio.Task[None] | None = None
        self._status = "disabled"
        self._detail = "no user_mode forwarding rules"
        self._last_error = ""
        self._retry_delay_seconds = max(0.01, float(retry_delay_seconds))

    def status(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "detail": self._detail,
            "user_rule_count": len(self._rules),
            "last_error": self._last_error,
        }

    async def reload(self, config: dict[str, Any]) -> None:
        await self._stop_task()
        self._rules = [rule for rule in parse_forwarding_rules(config.get("forwarding_rules", [])) if rule.mode == "user_mode"]
        if not self._rules:
            self._status = "disabled"
            self._detail = "no user_mode forwarding rules"
            self._last_error = ""
            return
        if self._db is None or self._engine is None:
            self._status = "fail"
            self._detail = "forwarding database is not configured"
            return
        self._status = "starting"
        self._detail = "connecting Telethon forwarding session"
        self._task = asyncio.create_task(self._supervise(config), name="user-forwarding-runtime")

    async def _supervise(self, config: dict[str, Any]) -> None:
        attempt = 0
        while True:
            try:
                await self._run_once(config)
                raise ConnectionError("Telethon forwarding client disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                self._status = "degraded"
                self._detail = f"retrying Telethon forwarding connection (attempt {attempt})"
                self._last_error = str(exc)
                logger.warning("user_forwarding_retry attempt=%s error=%s", attempt, exc, exc_info=True)
                await asyncio.sleep(min(60.0, self._retry_delay_seconds * (2 ** min(attempt - 1, 5))))

    async def _run_once(self, config: dict[str, Any]) -> None:
        client = None
        try:
            bot_config = get_bot_config(config)
            api_id, api_hash = get_api_credentials(bot_config)
            source_base = resolve_web_session_path(bot_config)
            target_base = resolve_app_path(
                str(bot_config.get("forwarding_tg_session") or "./sessions/forwarding")
            )
            _clone_session_if_needed(
                telethon_session_db_path(source_base),
                telethon_session_db_path(target_base),
            )
            client = self._client_factory(
                str(target_base),
                api_id,
                api_hash,
                auto_reconnect=True,
                connection_retries=5,
                retry_delay=2,
                flood_sleep_threshold=60,
            )
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("forwarding Telethon session is not authorized")

            from telethon import events

            source_ids = sorted({rule.source_id for rule in self._rules})

            async def on_album(event: Any) -> None:
                await self._engine.handle(int(event.chat_id), list(event.messages), client)

            async def on_message(event: Any) -> None:
                message = event.message
                if getattr(message, "grouped_id", None):
                    return
                await self._engine.handle(int(event.chat_id), [message], client)

            client.add_event_handler(on_album, events.Album(chats=source_ids))
            client.add_event_handler(on_message, events.NewMessage(chats=source_ids))
            self._status = "running"
            self._detail = f"listening to {len(source_ids)} source chats"
            self._last_error = ""
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._status = "degraded"
            self._detail = "Telethon forwarding connection failed"
            self._last_error = str(exc)
            raise
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    logger.warning("user_forwarding_disconnect_failed", exc_info=True)

    async def _stop_task(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        await self._stop_task()
        self._status = "stopped"
        self._detail = "runtime stopped"

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
