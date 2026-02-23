"""Main application entry point with both Telegram Bot and FastAPI Web Server."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core import scheduler
from app.core.config_manager import ConfigManager
from app.main import create_app
from tg_media_dedupe_bot import telegram_bot

logger = logging.getLogger(__name__)


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _apply_bot_env(overrides: dict[str, Any]) -> None:
    if not isinstance(overrides, dict):
        return
    if "bot_token" in overrides:
        value = str(overrides.get("bot_token") or "").strip()
        if value:
            os.environ["TG_BOT_TOKEN"] = value
        else:
            os.environ.pop("TG_BOT_TOKEN", None)
    if "api_id" in overrides:
        value = str(overrides.get("api_id") or "").strip()
        if value:
            os.environ["TG_API_ID"] = value
        else:
            os.environ.pop("TG_API_ID", None)
    if "api_hash" in overrides:
        value = str(overrides.get("api_hash") or "").strip()
        if value:
            os.environ["TG_API_HASH"] = value
        else:
            os.environ.pop("TG_API_HASH", None)
    if "dry_run" in overrides:
        value = _coerce_bool(overrides.get("dry_run"))
        if value is not None:
            os.environ["DRY_RUN"] = "true" if value else "false"
    if "delete_duplicates" in overrides:
        value = _coerce_bool(overrides.get("delete_duplicates"))
        if value is not None:
            os.environ["DELETE_DUPLICATES"] = "true" if value else "false"
    if "log_level" in overrides and str(overrides.get("log_level")).strip():
        os.environ["LOG_LEVEL"] = str(overrides.get("log_level")).strip().upper()
    if "tag_count" in overrides:
        value = _coerce_int(overrides.get("tag_count"))
        if value is not None:
            os.environ["TAG_COUNT"] = str(value)
    if "tag_build_limit" in overrides:
        value = _coerce_int(overrides.get("tag_build_limit"))
        if value is not None:
            os.environ["TAG_BUILD_LIMIT"] = str(value)


def _apply_media_filter_config(raw_config: dict[str, Any]) -> None:
    if not isinstance(raw_config, dict):
        raw_config = {}

    size_limit = _coerce_float(raw_config.get("size_limit_mb"))
    duration_limit = _coerce_float(raw_config.get("duration_limit_min"))
    filter_mode = str(raw_config.get("filter_mode", "off")).strip().lower()
    media_types_raw = raw_config.get("media_types", [])

    if filter_mode not in {"blacklist", "whitelist"}:
        filter_mode = "off"

    size_op = "gt" if size_limit and size_limit > 0 else None
    size_mb = float(size_limit) if size_limit and size_limit > 0 else None

    duration_op = "gt" if duration_limit and duration_limit > 0 else None
    duration_sec = int(duration_limit * 60) if duration_limit and duration_limit > 0 else None

    allowed_types = {"video", "audio", "photo", "document", "text"}
    type_set: set[str] = set()
    if isinstance(media_types_raw, list):
        for item in media_types_raw:
            if not isinstance(item, str):
                continue
            normalized = item.strip().lower()
            if normalized in allowed_types:
                type_set.add(normalized)

    if filter_mode == "off":
        type_set = set()

    settings = telegram_bot.MediaFilterSettings(
        size_op=size_op,
        size_mb=size_mb,
        duration_op=duration_op,
        duration_sec=duration_sec,
        type_mode=filter_mode,
        type_set=type_set,
        include_text=False,
    )

    telegram_bot.media_filter_default = settings
    for current in telegram_bot.media_filter_settings.values():
        current.size_op = settings.size_op
        current.size_mb = settings.size_mb
        current.duration_op = settings.duration_op
        current.duration_sec = settings.duration_sec
        current.type_mode = settings.type_mode
        current.type_set = set(settings.type_set)
        current.include_text = settings.include_text


async def reload_services(config: dict[str, Any]) -> None:
    """Reload runtime settings for bot-related services."""
    await scheduler.reload_scheduler_job(config)

    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}

    _apply_bot_env(bot_config)
    updated = telegram_bot.apply_runtime_config(bot_config)

    media_filters = config.get("web_media_filters", {}) if isinstance(config, dict) else {}
    _apply_media_filter_config(media_filters)

    log_level = str(bot_config.get("log_level") or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))
    logging.getLogger("tg_media_dedupe_bot").setLevel(getattr(logging, log_level, logging.INFO))

    if updated:
        logger.info("runtime_config_reloaded fields=%s", ",".join(updated))
    else:
        logger.info("runtime_config_reloaded fields=none")


async def run_web_server(config_manager: ConfigManager):
    """Run FastAPI web server in async context."""
    web_config = config_manager.get_config().get("web_admin", {})
    host = web_config.get("host", "0.0.0.0")
    port = web_config.get("port", 8000)
    
    # Create FastAPI app
    app = create_app(config_manager, reload_hook=reload_services)
    
    # Configure uvicorn
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    
    logger.info(f"Starting FastAPI web server on {host}:{port}")
    await server.serve()


async def main_async():
    """Run both bot and web server concurrently."""
    # Load configuration
    config_manager = ConfigManager("config.json")
    config = config_manager.get_config()
    _apply_bot_env(config.get("bot", {}))
    _apply_media_filter_config(config.get("web_media_filters", {}))
    
    # Setup logging
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    
    # Check if web admin is enabled
    web_config = config_manager.get_config().get("web_admin", {})
    web_enabled = web_config.get("enabled", True)
    
    if web_enabled:
        logger.info("Web admin panel is enabled")
        # Run both bot and web server
        bot_task = asyncio.create_task(asyncio.to_thread(telegram_bot.run_bot))
        web_task = asyncio.create_task(run_web_server(config_manager))
        
        await asyncio.gather(bot_task, web_task)
    else:
        logger.info("Web admin panel is disabled, running bot only")
        # Run only the bot
        await asyncio.to_thread(telegram_bot.run_bot)


def main():
    """Main entry point."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
