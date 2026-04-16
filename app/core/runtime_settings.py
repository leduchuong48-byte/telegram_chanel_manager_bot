from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _normalize_target_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        return [raw] if raw else []
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_normalize_target_tokens(item))
        seen: set[str] = set()
        ordered: list[str] = []
        for item in result:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered
    return []


@dataclass(slots=True)
class RuntimeSettings:
    dry_run: bool
    delete_duplicates: bool
    api_id: int
    api_hash: str
    bot_token: str
    admin_id: str
    target_chat_tokens: list[str]
    web_tg_session: str
    pipeline_worker_count: int


def load_runtime_settings(config: dict[str, Any]) -> RuntimeSettings:
    bot = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot, dict):
        bot = {}
    pipeline = config.get("pipeline", {}) if isinstance(config, dict) else {}
    if not isinstance(pipeline, dict):
        pipeline = {}

    target_tokens = _normalize_target_tokens(bot.get("target_chat_ids"))
    if not target_tokens:
        target_tokens = _normalize_target_tokens(bot.get("target_chat_id"))

    return RuntimeSettings(
        dry_run=_coerce_bool(bot.get("dry_run"), default=True),
        delete_duplicates=_coerce_bool(bot.get("delete_duplicates"), default=False),
        api_id=_coerce_int(bot.get("api_id"), default=0),
        api_hash=str(bot.get("api_hash") or "").strip(),
        bot_token=str(bot.get("bot_token") or "").strip(),
        admin_id=str(bot.get("admin_id") or "").strip(),
        target_chat_tokens=target_tokens,
        web_tg_session=str(bot.get("web_tg_session") or "./sessions/webui").strip(),
        pipeline_worker_count=max(1, _coerce_int(pipeline.get("worker_count"), default=1)),
    )
