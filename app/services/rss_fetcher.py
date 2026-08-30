"""RSS fetcher service entrypoint."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config_manager import ConfigManager
from tg_media_dedupe_bot.telethon_scan import run_scan

logger = logging.getLogger(__name__)


def _parse_int(config: dict[str, Any], key: str, default: int) -> int:
    raw = config.get(key, default)
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _parse_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    raw = config.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


async def run_full_scan() -> None:
    """Simulate a full RSS scan."""
    logger.info("开始执行 RSS 扫描任务...")

    config = ConfigManager.get_instance().get_config()
    if not isinstance(config, dict):
        logger.warning("配置加载失败，跳过 RSS 扫描")
        return

    chat_id = config.get("rss_chat_id")
    limit_count = _parse_int(config, "rss_limit", 100)
    delete_enabled = _parse_bool(config, "rss_delete", False)
    reverse_enabled = _parse_bool(config, "rss_reverse", False)
    as_bot_enabled = _parse_bool(config, "rss_as_bot", False)

    if chat_id is None or str(chat_id).strip() == "":
        logger.warning("未配置 rss_chat_id，跳过扫描")
        return

    chat_value = str(chat_id).strip()

    try:
        await run_scan(
            chat=chat_value,
            limit=limit_count,
            delete=delete_enabled,
            reverse=reverse_enabled,
            as_bot=as_bot_enabled,
            interactive=False,
        )
        logger.info("RSS 扫描任务完成")
    except Exception as exc:  # noqa: BLE001
        logger.error("RSS 扫描任务执行失败: %s", exc, exc_info=True)
