from __future__ import annotations

import asyncio
import json
import html
import http.server
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tg_media_dedupe_bot import __version__
from tg_media_dedupe_bot.config import Config, load_config
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.models import MediaItem, ProcessDecision
from tg_media_dedupe_bot.telethon_scan import ScanResult, run_scan
from tg_media_dedupe_bot.telethon_tags import (
    TagScanProgress,
    TagScanResult,
    _extract_hashtags as _extract_hashtags_telethon,
    _resolve_entity as _resolve_entity_telethon,
    run_tag_scan,
)

_RUNTIME_CONFIG: Config | None = None
_RUNTIME_CONFIG_LOCK = threading.Lock()
_MANAGED_CHAT_TYPES = {"group", "supergroup", "channel"}
_BOT_MANAGE_STATUSES = {"administrator", "creator"}
_INACTIVE_MEMBER_STATUSES = {"left", "kicked"}


def set_runtime_config(config: Config) -> None:
    """Expose the current bot config for hot reload updates."""
    global _RUNTIME_CONFIG
    with _RUNTIME_CONFIG_LOCK:
        _RUNTIME_CONFIG = config


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


def apply_runtime_config(overrides: dict[str, Any]) -> list[str]:
    """Apply runtime overrides to the running bot config."""
    if not isinstance(overrides, dict):
        return []
    with _RUNTIME_CONFIG_LOCK:
        config = _RUNTIME_CONFIG
        if config is None:
            return []
        updated: list[str] = []

        if "dry_run" in overrides:
            value = _coerce_bool(overrides.get("dry_run"))
            if value is not None:
                config.dry_run = value
                updated.append("dry_run")

        if "delete_duplicates" in overrides:
            value = _coerce_bool(overrides.get("delete_duplicates"))
            if value is not None:
                config.delete_duplicates = value
                updated.append("delete_duplicates")

        if "log_level" in overrides:
            raw = overrides.get("log_level")
            if isinstance(raw, str) and raw.strip():
                config.log_level = raw.strip().upper()
                updated.append("log_level")

        if "tag_count" in overrides:
            value = _coerce_int(overrides.get("tag_count"))
            if value is not None:
                value = max(1, min(10, value))
                config.tag_count = value
                updated.append("tag_count")

        if "tag_build_limit" in overrides:
            value = _coerce_int(overrides.get("tag_build_limit"))
            if value is not None:
                config.tag_build_limit = max(0, value)
                updated.append("tag_build_limit")

        return updated


def _extract_media_item(message: Message) -> MediaItem | None:
    if message.chat is None:
        return None

    chat_id = int(message.chat.id)
    message_id = int(message.message_id)
    message_date = int(message.date.timestamp()) if message.date else 0
    is_forwarded = _is_forwarded_message(message)

    if message.photo:
        photo = message.photo[-1]
        file_unique_id = getattr(photo, "file_unique_id", None)
        file_id = getattr(photo, "file_id", None)
        if not file_unique_id:
            return None
        media_key = f"botapi:photo:{file_unique_id}"
        return MediaItem(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            media_type="photo",
            file_unique_id=file_unique_id,
            file_id=file_id,
            message_date=message_date,
            is_forwarded=is_forwarded,
        )

    attachment_map: list[tuple[str, Any]] = [
        ("video", message.video),
        ("animation", message.animation),
        ("document", message.document),
        ("audio", message.audio),
        ("voice", message.voice),
        ("video_note", message.video_note),
        ("sticker", message.sticker),
    ]

    for media_type, obj in attachment_map:
        if obj is None:
            continue
        file_unique_id = getattr(obj, "file_unique_id", None)
        file_id = getattr(obj, "file_id", None)
        if not file_unique_id:
            continue
        media_key = f"botapi:{media_type}:{file_unique_id}"
        return MediaItem(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            media_type=media_type,
            file_unique_id=file_unique_id,
            file_id=file_id,
            message_date=message_date,
            is_forwarded=is_forwarded,
        )

    return None


def _chat_allowed(config: Config, chat_id: int) -> bool:
    if config.allow_chat_ids is None:
        return True
    return chat_id in config.allow_chat_ids


def _chat_setting_key(chat_id: int, name: str) -> str:
    return f"chat:{chat_id}:{name}"


def _parse_bool_setting(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _parse_int_setting(value: str | None, *, min_value: int, max_value: int) -> int | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def _media_blacklist_key(chat_id: int) -> str:
    return _chat_setting_key(chat_id, "media_blacklist")


def _parse_media_blacklist(raw: str | None) -> set[str]:
    if not raw:
        return set()
    items = [part.strip().lower() for part in raw.split(",")]
    return {item for item in items if item in _MEDIA_BLACKLIST_TYPES}


def _serialize_media_blacklist(items: set[str]) -> str:
    if not items:
        return ""
    return ",".join(sorted(items))


def _normalize_media_type(raw_type: str) -> str | None:
    if raw_type in {"video", "animation", "video_note"}:
        return "video"
    if raw_type in {"audio", "voice"}:
        return "audio"
    if raw_type == "photo":
        return "photo"
    if raw_type in {"document", "sticker"}:
        return "document"
    return None


def _is_forwarded_message(message: Message) -> bool:
    return bool(getattr(message, "forward_origin", None))


def _contains_ad_text(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    if _AD_LINK_RE.search(text):
        return True
    folded = text.casefold()
    for keyword in keywords:
        if keyword and keyword.casefold() in folded:
            return True
    return False


def _is_telethon_forwarded(message: Any) -> bool:
    return bool(getattr(message, "fwd_from", None))


def _telethon_media_type(message: Any) -> str | None:
    if getattr(message, "photo", None) is not None:
        return "photo"
    document = getattr(message, "document", None)
    if document is None:
        return None
    mime_type = getattr(document, "mime_type", "") or ""
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _telethon_media_size(message: Any) -> int | None:
    photo = getattr(message, "photo", None)
    if photo is not None:
        sizes = getattr(photo, "sizes", None) or []
        max_size: int | None = None
        for size in sizes:
            value = getattr(size, "size", None)
            if isinstance(value, int):
                if max_size is None or value > max_size:
                    max_size = value
        return max_size
    document = getattr(message, "document", None)
    if document is None:
        return None
    value = getattr(document, "size", None)
    if isinstance(value, int):
        return value
    return None


def _telethon_media_duration(message: Any) -> int | None:
    document = getattr(message, "document", None)
    if document is None:
        return None
    attributes = getattr(document, "attributes", None) or []
    for attr in attributes:
        duration = getattr(attr, "duration", None)
        if duration is None:
            continue
        try:
            return int(duration)
        except (TypeError, ValueError):
            continue
    return None


_HASHTAG_RE = re.compile(r"(?<!\w)#(\w{1,64})")
_INVALID_TAG_RE = re.compile(r"^(?:\d+|(?=.*\d)(?=.*[A-Za-z])[0-9A-Za-z]+)$")
_AD_LINK_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.IGNORECASE)
_MEDIA_BLACKLIST_TYPES = ("video", "audio", "photo", "text", "document")


def _is_valid_tag(tag: str) -> bool:
    if not tag:
        return False
    return _INVALID_TAG_RE.match(tag) is None


def _extract_hashtags_bot(text: str, entities: list[Any] | None) -> list[str]:
    if not text:
        return []
    if entities:
        tags: list[str] = []
        for ent in entities:
            if str(getattr(ent, "type", "")).lower() != "hashtag":
                continue
            offset = int(getattr(ent, "offset", 0))
            length = int(getattr(ent, "length", 0))
            if length <= 1:
                continue
            raw = text[offset : offset + length]
            if not raw.startswith("#") or len(raw) <= 1:
                continue
            tag = raw[1:]
            if tag and _is_valid_tag(tag):
                tags.append(tag)
        if tags:
            return tags
    return [m.group(1) for m in _HASHTAG_RE.finditer(text) if _is_valid_tag(m.group(1))]


def _apply_text_block(text: str, keywords: list[str]) -> str:
    if not text:
        return ""
    cleaned = text
    for keyword in keywords:
        if not keyword:
            continue
        cleaned = re.sub(re.escape(keyword), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _match_tags(text: str, tag_counts: list[tuple[str, int]], *, max_tags: int) -> list[str]:
    if not text or max_tags <= 0:
        return []
    folded = text.casefold()
    matches: list[str] = []
    for tag, _count in tag_counts:
        if not tag or not _is_valid_tag(tag):
            continue
        if tag in folded:
            matches.append(tag)
            if len(matches) >= max_tags:
                break
    return matches


def _match_tags_excluding(
    text: str,
    tag_counts: list[tuple[str, int]],
    *,
    max_tags: int,
    exclude: list[str],
) -> list[str]:
    if not text or max_tags <= 0:
        return []
    folded = text.casefold()
    excluded = {_normalize_tag_text(tag) for tag in exclude if tag}
    matches: list[str] = []
    for tag, _count in tag_counts:
        normalized = _normalize_tag_text(tag)
        if not normalized or normalized in excluded or not _is_valid_tag(normalized):
            continue
        if normalized in folded:
            matches.append(normalized)
            if len(matches) >= max_tags:
                break
    return matches


def _build_tag_caption(text: str, tags: list[str]) -> str:
    tag_line = " ".join(f"#{tag}" for tag in tags if tag)
    if not text:
        return tag_line
    if not tag_line:
        return text
    return f"{text}\n\n{tag_line}"


def _normalize_tag_text(tag: str) -> str:
    return tag.strip().lstrip("#").casefold()


def _strip_hashtags(text: str) -> str:
    if not text:
        return ""
    return _HASHTAG_RE.sub("", text)


def _wrap_hashtags(tokens: list[str], *, max_line_len: int = 120) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in tokens:
        if not current:
            current = token
            continue
        if len(current) + 1 + len(token) <= max_line_len:
            current += f" {token}"
            continue
        lines.append(current)
        current = token
    if current:
        lines.append(current)
    return lines


def _tag_aliases_path(chat_id: int) -> Path:
    root = Path("data") / "tag_aliases"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{chat_id}.txt"


def _tag_aliases_global_path() -> Path:
    root = Path("data") / "tag_aliases"
    root.mkdir(parents=True, exist_ok=True)
    return root / "global.txt"


def _tag_groups_path(chat_id: int) -> Path:
    root = Path("data") / "tag_groups"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{chat_id}.txt"


def _text_block_dir() -> Path:
    root = Path("data") / "text_block"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _text_block_global_path() -> Path:
    return _text_block_dir() / "global.txt"


def _text_block_chat_path(chat_id: int) -> Path:
    return _text_block_dir() / f"{chat_id}.txt"


def _parse_text_block_file(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//") or line.startswith(";"):
            continue
        normalized = line.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(line)
    return keywords


def _write_text_block_file(path: Path, keywords: list[str]) -> None:
    lines = [kw.strip() for kw in keywords if kw and kw.strip()]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _merge_text_block_keywords(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in group:
            cleaned = keyword.strip()
            if not cleaned:
                continue
            normalized = cleaned.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(cleaned)
    return merged


def _collect_text_block_keywords(*, chat_id: int, db_keywords: list[str]) -> list[str]:
    global_keywords = _parse_text_block_file(_text_block_global_path())
    chat_keywords = _parse_text_block_file(_text_block_chat_path(chat_id))
    return _merge_text_block_keywords(db_keywords, global_keywords, chat_keywords)


def _parse_tag_alias_file(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    mapping: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        left, right = line.split("=", 1)
        old_tag = _normalize_tag_text(left)
        new_tag = _normalize_tag_text(right)
        if not old_tag or not new_tag or old_tag == new_tag:
            continue
        mapping[old_tag] = new_tag
    return mapping


def _write_tag_alias_file(path: Path, mapping: dict[str, str]) -> None:
    lines: list[str] = []
    for old_tag in sorted(mapping.keys()):
        new_tag = mapping[old_tag]
        lines.append(f"#{old_tag}=#{new_tag}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _apply_tag_aliases(tags: list[str], mapping: dict[str, str]) -> list[str]:
    normalized = [_normalize_tag_text(tag) for tag in tags]
    result: list[str] = []
    seen: set[str] = set()
    for tag in normalized:
        if not tag:
            continue
        mapped = mapping.get(tag, tag)
        if mapped in seen:
            continue
        seen.add(mapped)
        result.append(mapped)
    return result


def _apply_tag_aliases_to_counts(
    tag_counts: dict[str, int],
    mapping: dict[str, str],
) -> dict[str, int]:
    merged: dict[str, int] = {}
    for tag, count in tag_counts.items():
        key = _normalize_tag_text(tag)
        if not key:
            continue
        key = mapping.get(key, key)
        merged[key] = merged.get(key, 0) + int(count)
    return merged


def _parse_tag_groups_file(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return [], {}
    groups: list[str] = []
    group_tags: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        if line.startswith("---") and line.endswith("---"):
            name = line.strip("-").strip()
            if not name:
                continue
            current = name
            if current not in group_tags:
                groups.append(current)
                group_tags[current] = []
            continue
        if current is None:
            continue
        parts = [part.strip() for part in re.split(r"[,\s]+", line) if part.strip()]
        for part in parts:
            tag = _normalize_tag_text(part)
            if not tag:
                continue
            if tag not in group_tags[current]:
                group_tags[current].append(tag)
    return groups, group_tags


def _write_tag_groups_file(path: Path, groups: list[str], group_tags: dict[str, list[str]]) -> None:
    lines: list[str] = []
    for group in groups:
        tags = group_tags.get(group, [])
        lines.append(f"-------{group}--------")
        for tag in tags:
            lines.append(f"#{tag}")
        lines.append("")
    while lines and not lines[-1].strip():
        lines.pop()
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


@dataclass
class ScanState:
    started_at: int
    limit: int
    delete: bool
    scanned: int = 0
    decided_delete: int = 0
    deleted: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class TagSummaryState:
    started_at: int
    limit: int
    max_tags: int
    scanned: int = 0
    unique_tags: int = 0
    total_tags: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class TagBuildState:
    started_at: int
    limit: int
    scanned: int = 0
    tagged: int = 0
    skipped: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class TagRebuildState:
    started_at: int
    limit: int
    scanned: int = 0
    rebuilt: int = 0
    skipped: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class TextPurgeState:
    started_at: int
    limit: int
    scanned: int = 0
    deleted: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class DeleteByTextInput:
    user_id: int
    keyword: str | None = None
    step: str = "await_input"


@dataclass
class TagAddInput:
    user_id: int
    step: str = "await_input"


@dataclass
class BatchDeleteInput:
    user_id: int
    count: int | None = None
    step: str = "await_input"


@dataclass
class DeleteByTextState:
    started_at: int
    keyword: str
    scanned: int = 0
    deleted: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class BatchDeleteState:
    started_at: int
    limit: int
    scanned: int = 0
    deleted: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class MediaFilterSettings:
    size_op: str | None = None
    size_mb: float | None = None
    duration_op: str | None = None
    duration_sec: int | None = None
    type_mode: str = "off"
    type_set: set[str] = field(default_factory=set)
    include_text: bool = False


media_filter_settings: dict[int, MediaFilterSettings] = {}
media_filter_default: MediaFilterSettings | None = None


@dataclass
class MediaFilterInput:
    user_id: int
    step: str = "menu"
    pending_kind: str | None = None
    pending_op: str | None = None
    snapshot: MediaFilterSettings | None = None


@dataclass
class MediaFilterState:
    started_at: int
    scanned: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0
    running: bool = True
    last_error: str | None = None
    progress_message_id: int | None = None
    last_progress_at: int = 0


@dataclass
class PendingLogin:
    phone: str
    phone_code_hash: str
    started_at: int
    needs_password: bool = False


async def _cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="pong")


def run_bot() -> None:
    config = load_config()
    set_runtime_config(config)
    if not config.bot_token:
        raise RuntimeError("缺少环境变量 TG_BOT_TOKEN")

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("tg_media_dedupe_bot")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    def _load_config_json() -> dict[str, Any]:
        path = Path("config.json")
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("config_json_load_failed error=%s", exc)
            return {}

    def _resolve_web_panel_url(config_data: dict[str, Any]) -> str | None:
        web_admin = config_data.get("web_admin", {})
        if not isinstance(web_admin, dict):
            return None
        if web_admin.get("enabled") is False:
            return None
        host = str(web_admin.get("host") or "").strip()
        port_raw = web_admin.get("port")
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            return None
        if not host or port <= 0:
            return None
        return f"http://{host}:{port}"

    def _resolve_admin_id(config_data: dict[str, Any]) -> int | None:
        bot_config = config_data.get("bot", {})
        if not isinstance(bot_config, dict):
            return None
        raw = str(bot_config.get("admin_id") or "").strip()
        if not raw:
            return None
        if not raw.lstrip("-").isdigit():
            return None
        return int(raw)

    config_snapshot = _load_config_json()
    web_panel_url = _resolve_web_panel_url(config_snapshot)
    admin_id = _resolve_admin_id(config_snapshot)

    def _start_health_server() -> None:
        port_raw = os.getenv("HEALTH_PORT", "8080").strip()
        if not port_raw or port_raw == "0":
            return
        try:
            port = int(port_raw)
        except ValueError:
            log.warning("health_server_port_invalid value=%s", port_raw)
            return
        bind = os.getenv("HEALTH_BIND", "0.0.0.0").strip() or "0.0.0.0"

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                log.debug("health_server %s", format % args)

        try:
            server = http.server.ThreadingHTTPServer((bind, port), HealthHandler)
        except OSError:
            log.exception("health_server_start_failed bind=%s port=%s", bind, port)
            return
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log.info("health_server_started bind=%s port=%s", bind, port)

    _start_health_server()

    db = Database(config.db_path)
    def _migrate_text_block_to_file() -> None:
        keywords = db.list_text_block_keywords()
        if not keywords:
            return
        path = _text_block_global_path()
        try:
            file_keywords = _parse_text_block_file(path)
            merged = _merge_text_block_keywords(file_keywords, keywords)
            if merged != file_keywords:
                _write_text_block_file(path, merged)
            for keyword in keywords:
                db.remove_text_block_keyword(keyword)
            log.info("text_block_migrated count=%s file=%s", len(keywords), path)
        except Exception:  # noqa: BLE001
            log.exception("text_block_migrate_failed")

    _migrate_text_block_to_file()

    def _bootstrap_managed_chat_registry() -> None:
        try:
            existing = db.list_managed_chats(active_only=False, manageable_only=False, limit=1)
            if existing:
                return
            known_chat_ids = db.list_known_chat_ids(limit=2000)
            if not known_chat_ids:
                return
            for chat_id in known_chat_ids:
                db.upsert_managed_chat(
                    chat_id=chat_id,
                    title=str(chat_id),
                    username="",
                    chat_type="unknown",
                    source="bootstrap_db",
                    is_active=True,
                    bot_status="unchecked",
                    bot_can_manage=True,
                    verified_at=0,
                    verified_by="bootstrap_db",
                )
            log.info("managed_chats_bootstrapped count=%s", len(known_chat_ids))
        except Exception:  # noqa: BLE001
            log.exception("managed_chats_bootstrap_failed")

    _bootstrap_managed_chat_registry()
    db_lock = asyncio.Lock()
    telethon_lock = asyncio.Lock()
    scan_tasks: dict[int, asyncio.Task] = {}
    scan_states: dict[int, ScanState] = {}
    tag_tasks: dict[int, asyncio.Task] = {}
    tag_states: dict[int, TagSummaryState] = {}
    tag_build_tasks: dict[int, asyncio.Task] = {}
    tag_build_states: dict[int, TagBuildState] = {}
    tag_rebuild_tasks: dict[int, asyncio.Task] = {}
    tag_rebuild_states: dict[int, TagRebuildState] = {}
    text_purge_tasks: dict[int, asyncio.Task] = {}
    text_purge_states: dict[int, TextPurgeState] = {}
    delete_by_text_inputs: dict[int, DeleteByTextInput] = {}
    batch_delete_inputs: dict[int, BatchDeleteInput] = {}
    tag_add_inputs: dict[int, TagAddInput] = {}
    media_filter_inputs: dict[int, MediaFilterInput] = {}
    delete_by_text_tasks: dict[int, asyncio.Task] = {}
    delete_by_text_states: dict[int, DeleteByTextState] = {}
    batch_delete_tasks: dict[int, asyncio.Task] = {}
    batch_delete_states: dict[int, BatchDeleteState] = {}
    media_filter_tasks: dict[int, asyncio.Task] = {}
    media_filter_states: dict[int, MediaFilterState] = {}
    login_states: dict[int, PendingLogin] = {}
    qr_tasks: dict[int, asyncio.Task] = {}

    def _normalize_chat_type(chat: Any) -> str:
        return str(getattr(chat, "type", "") or "").strip().lower()

    def _is_trackable_chat(chat: Any) -> bool:
        return _normalize_chat_type(chat) in _MANAGED_CHAT_TYPES

    def _chat_title(chat: Any, fallback_chat_id: int) -> str:
        title = str(getattr(chat, "title", "") or "").strip()
        if title:
            return title
        first_name = str(getattr(chat, "first_name", "") or "").strip()
        last_name = str(getattr(chat, "last_name", "") or "").strip()
        full_name = " ".join(part for part in [first_name, last_name] if part)
        if full_name:
            return full_name
        username = str(getattr(chat, "username", "") or "").strip().lstrip("@")
        if username:
            return f"@{username}"
        return str(fallback_chat_id)

    async def _upsert_managed_chat(
        chat: Any,
        *,
        source: str,
        is_active: bool = True,
        bot_status: str | None = None,
        bot_can_manage: bool | None = None,
        verified_at: int | None = None,
        verified_by: str | None = None,
    ) -> None:
        if chat is None or not _is_trackable_chat(chat):
            return
        chat_id_raw = getattr(chat, "id", None)
        if not isinstance(chat_id_raw, int):
            return
        chat_id = int(chat_id_raw)
        chat_type = _normalize_chat_type(chat)
        title = _chat_title(chat, chat_id)
        username = str(getattr(chat, "username", "") or "").strip().lstrip("@")
        try:
            async with db_lock:
                db.upsert_managed_chat(
                    chat_id=chat_id,
                    title=title,
                    username=username,
                    chat_type=chat_type,
                    source=source,
                    is_active=is_active,
                    bot_status=bot_status,
                    bot_can_manage=bot_can_manage,
                    verified_at=verified_at,
                    verified_by=verified_by,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("managed_chat_upsert_failed chat=%s source=%s error=%s", chat_id, source, exc)

    async def _on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        member_update = update.my_chat_member
        if member_update is None:
            return
        chat = getattr(member_update, "chat", None)
        if chat is None:
            return
        new_member = getattr(member_update, "new_chat_member", None)
        status = str(getattr(new_member, "status", "") or "").strip().lower()
        if not status:
            status = "unknown"
        is_active = status not in _INACTIVE_MEMBER_STATUSES
        bot_can_manage = status in _BOT_MANAGE_STATUSES
        await _upsert_managed_chat(
            chat,
            source="my_chat_member",
            is_active=is_active,
            bot_status=status,
            bot_can_manage=bot_can_manage,
            verified_at=int(time.time()),
            verified_by="my_chat_member",
        )

    async def _track_message_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        chat = update.effective_chat
        if chat is None:
            return
        await _upsert_managed_chat(chat, source="message", is_active=True)

    async def _effective_mode(chat_id: int) -> tuple[bool, bool]:
        async with db_lock:
            dry_raw = db.get_setting(_chat_setting_key(chat_id, "dry_run"))
            del_raw = db.get_setting(_chat_setting_key(chat_id, "delete_duplicates"))
        dry = _parse_bool_setting(dry_raw)
        delete_enabled = _parse_bool_setting(del_raw)
        return (
            config.dry_run if dry is None else dry,
            config.delete_duplicates if delete_enabled is None else delete_enabled,
        )

    async def _effective_tag_count(chat_id: int) -> int:
        async with db_lock:
            raw = db.get_setting(_chat_setting_key(chat_id, "tag_count"))
        parsed = _parse_int_setting(raw, min_value=1, max_value=10)
        return config.tag_count if parsed is None else parsed

    async def _effective_media_blacklist(chat_id: int) -> set[str]:
        async with db_lock:
            raw = db.get_setting(_media_blacklist_key(chat_id))
        return _parse_media_blacklist(raw)

    def _tag_count_markup(current: int) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for value in range(1, 11):
            label = f"{value}" + (" ✅" if value == current else "")
            row.append(InlineKeyboardButton(label, callback_data=f"cm:set_tag_count:{value}"))
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton("返回", callback_data="cm:menu")])
        return InlineKeyboardMarkup(rows)

    def _media_blacklist_markup(selected: set[str]) -> InlineKeyboardMarkup:
        rows = []
        labels = {
            "video": "视频",
            "audio": "音频",
            "photo": "图片",
            "text": "纯文字",
            "document": "文档",
        }
        for media_type in _MEDIA_BLACKLIST_TYPES:
            label = labels.get(media_type, media_type)
            prefix = "✅ " if media_type in selected else ""
            rows.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cm:toggle_media:{media_type}")])
        rows.append([InlineKeyboardButton("返回", callback_data="cm:menu")])
        return InlineKeyboardMarkup(rows)

    def _delete_by_text_prompt_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("取消", callback_data="cm:delete_by_text_cancel")]])

    def _delete_by_text_confirm_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("确认", callback_data="cm:delete_by_text_confirm"),
                    InlineKeyboardButton("取消", callback_data="cm:delete_by_text_cancel"),
                ]
            ]
        )

    def _batch_delete_prompt_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("取消", callback_data="cm:batch_delete_cancel")]])

    def _batch_delete_confirm_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("确认", callback_data="cm:batch_delete_confirm"),
                    InlineKeyboardButton("取消", callback_data="cm:batch_delete_cancel"),
                ]
            ]
        )

    def _tag_add_prompt_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("取消", callback_data="cm:tag_add_cancel")]])

    def _get_media_filter_settings(chat_id: int) -> MediaFilterSettings:
        settings = media_filter_settings.get(chat_id)
        if settings is None:
            if media_filter_default is None:
                settings = MediaFilterSettings()
            else:
                settings = _clone_media_filter_settings(media_filter_default)
            media_filter_settings[chat_id] = settings
        return settings

    def _format_media_type_list(items: set[str]) -> str:
        if not items:
            return "无"
        labels = {
            "video": "视频",
            "audio": "音频",
            "photo": "图片",
            "text": "纯文字",
            "document": "文档",
        }
        ordered = sorted(items)
        return ",".join(labels.get(item, item) for item in ordered)

    def _format_media_filter_size(settings: MediaFilterSettings) -> str:
        if settings.size_op and settings.size_mb is not None:
            op_label = "大于" if settings.size_op == "gt" else "小于"
            value = settings.size_mb
            display = f"{value:g}" if not float(value).is_integer() else str(int(value))
            return f"{op_label} {display}MB"
        return "未设置"

    def _format_duration_minutes(value: int) -> str:
        minutes = value / 60
        if minutes.is_integer():
            return str(int(minutes))
        return f"{minutes:g}"

    def _format_media_filter_duration(settings: MediaFilterSettings) -> str:
        if settings.duration_op and settings.duration_sec is not None:
            op_label = "大于" if settings.duration_op == "gt" else "小于"
            minutes = _format_duration_minutes(int(settings.duration_sec))
            return f"{op_label} {minutes}分钟"
        return "未设置"

    def _clone_media_filter_settings(settings: MediaFilterSettings) -> MediaFilterSettings:
        return MediaFilterSettings(
            size_op=settings.size_op,
            size_mb=settings.size_mb,
            duration_op=settings.duration_op,
            duration_sec=settings.duration_sec,
            type_mode=settings.type_mode,
            type_set=set(settings.type_set),
            include_text=settings.include_text,
        )

    def _format_media_filter_type(settings: MediaFilterSettings) -> str:
        mode_label = {
            "off": "关闭",
            "blacklist": "黑名单",
            "whitelist": "白名单",
        }.get(settings.type_mode, "关闭")
        return f"{mode_label}（{_format_media_type_list(settings.type_set)}）"

    def _format_media_filter_summary(settings: MediaFilterSettings) -> str:
        size_text = _format_media_filter_size(settings)
        duration_text = _format_media_filter_duration(settings)
        type_text = _format_media_filter_type(settings)
        text_flag = "是" if settings.include_text else "否"

        return "\n".join(
            [
                f"- 媒体大小：{size_text}",
                f"- 媒体时长：{duration_text}",
                f"- 媒体类型：{type_text}",
                f"- 筛选纯文字：{text_flag}",
            ]
        )

    def _media_filter_menu_markup(settings: MediaFilterSettings) -> InlineKeyboardMarkup:
        size_label = "媒体大小"
        if settings.size_op and settings.size_mb is not None:
            op_label = ">" if settings.size_op == "gt" else "<"
            value = settings.size_mb
            display = f"{value:g}" if not float(value).is_integer() else str(int(value))
            size_label = f"媒体大小: {op_label} {display}MB"

        duration_label = "媒体时长"
        if settings.duration_op and settings.duration_sec is not None:
            op_label = ">" if settings.duration_op == "gt" else "<"
            minutes = _format_duration_minutes(int(settings.duration_sec))
            duration_label = f"媒体时长: {op_label} {minutes}分钟"

        text_label = "筛选纯文字: 开" if settings.include_text else "筛选纯文字: 关"

        buttons = [
            [InlineKeyboardButton(size_label, callback_data="cm:media_filter_size")],
            [InlineKeyboardButton(duration_label, callback_data="cm:media_filter_duration")],
            [InlineKeyboardButton("媒体类型", callback_data="cm:media_filter_type")],
            [InlineKeyboardButton(text_label, callback_data="cm:media_filter_text_toggle")],
            [InlineKeyboardButton("开始筛选", callback_data="cm:media_filter_start")],
            [InlineKeyboardButton("返回", callback_data="cm:menu")],
        ]
        return InlineKeyboardMarkup(buttons)

    def _media_filter_input_cancel_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("取消", callback_data="cm:media_filter_cancel")]])

    def _media_filter_size_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("大于", callback_data="cm:media_filter_size_op:gt"),
                    InlineKeyboardButton("小于", callback_data="cm:media_filter_size_op:lt"),
                ],
                [InlineKeyboardButton("清除", callback_data="cm:media_filter_size_clear")],
                [InlineKeyboardButton("返回", callback_data="cm:media_filter")],
            ]
        )

    def _media_filter_duration_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("大于", callback_data="cm:media_filter_duration_op:gt"),
                    InlineKeyboardButton("小于", callback_data="cm:media_filter_duration_op:lt"),
                ],
                [InlineKeyboardButton("清除", callback_data="cm:media_filter_duration_clear")],
                [InlineKeyboardButton("返回", callback_data="cm:media_filter")],
            ]
        )

    def _media_filter_type_markup(settings: MediaFilterSettings) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        mode_labels = [
            ("off", "关闭"),
            ("blacklist", "黑名单"),
            ("whitelist", "白名单"),
        ]
        mode_row: list[InlineKeyboardButton] = []
        for key, label in mode_labels:
            prefix = "✅ " if settings.type_mode == key else ""
            mode_row.append(InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cm:media_filter_type_mode:{key}"))
        rows.append(mode_row)

        labels = {
            "video": "视频",
            "audio": "音频",
            "photo": "图片",
            "text": "纯文字",
            "document": "文档",
        }
        for media_type in _MEDIA_BLACKLIST_TYPES:
            label = labels.get(media_type, media_type)
            prefix = "✅ " if media_type in settings.type_set else ""
            rows.append(
                [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cm:media_filter_type_toggle:{media_type}")]
            )
        rows.append([InlineKeyboardButton("返回", callback_data="cm:media_filter")])
        return InlineKeyboardMarkup(rows)

    def _media_filter_confirm_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("确认", callback_data="cm:media_filter_confirm"),
                    InlineKeyboardButton("取消", callback_data="cm:media_filter_cancel"),
                ]
            ]
        )

    def _parse_positive_float(raw: str) -> float | None:
        try:
            value = float(raw)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    def _parse_positive_int(raw: str) -> int | None:
        try:
            value = int(raw)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    def _media_filter_matches(
        settings: MediaFilterSettings,
        *,
        media_type: str,
        is_text: bool,
        size_bytes: int | None,
        duration_sec: int | None,
    ) -> bool:
        if settings.type_mode == "blacklist" and settings.type_set and media_type in settings.type_set:
            return False
        if settings.type_mode == "whitelist" and settings.type_set and media_type not in settings.type_set:
            return False
        if is_text:
            return True
        if settings.size_op and settings.size_mb is not None and size_bytes is not None:
            threshold = float(settings.size_mb) * 1024 * 1024
            if settings.size_op == "gt" and size_bytes <= threshold:
                return False
            if settings.size_op == "lt" and size_bytes >= threshold:
                return False
        if settings.duration_op and settings.duration_sec is not None and duration_sec is not None:
            if settings.duration_op == "gt" and duration_sec <= settings.duration_sec:
                return False
            if settings.duration_op == "lt" and duration_sec >= settings.duration_sec:
                return False
        return True

    async def _send_main_menu(
        *,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        edit_message_id: int | None = None,
    ) -> None:
        tag_count = await _effective_tag_count(chat_id)
        blacklist = await _effective_media_blacklist(chat_id)
        text = (
            "请选择操作：\n"
            f"- 补标签数量：{tag_count}\n"
            f"- 媒体黑名单：{','.join(sorted(blacklist)) if blacklist else '无'}\n"
            "- 标签更新：清理黑名单/文本并补齐标签后生成目录\n"
            "- 新建标签：添加到标签库\n"
            "- 删除特定媒体：删除包含指定文本的消息\n"
            "- 批量删除：从最新开始删除指定条数\n"
            "- 媒体筛选：按大小/时长/类型筛选并删除不符合项"
        )
        buttons = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"补标签数量: {tag_count}", callback_data="cm:tag_count")],
                [InlineKeyboardButton("媒体类型黑名单", callback_data="cm:media_blacklist")],
                [InlineKeyboardButton("标签更新", callback_data="cm:tag_update")],
                [InlineKeyboardButton("新建标签", callback_data="cm:tag_add")],
                [InlineKeyboardButton("删除特定媒体", callback_data="cm:delete_by_text")],
                [InlineKeyboardButton("批量删除（最新）", callback_data="cm:batch_delete")],
                [InlineKeyboardButton("清理全部纯文字", callback_data="cm:text_purge")],
                [InlineKeyboardButton("媒体筛选", callback_data="cm:media_filter")],
                [InlineKeyboardButton("关闭", callback_data="cm:close")],
            ]
        )
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=buttons,
            )
            return
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=buttons)

    async def _send_media_filter_menu(
        *,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        edit_message_id: int | None = None,
    ) -> None:
        settings = _get_media_filter_settings(chat_id)
        text = "媒体筛选设置：\n" + _format_media_filter_summary(settings) + "\n\n选择完成后点击“开始筛选”。"
        buttons = _media_filter_menu_markup(settings)
        if edit_message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=buttons,
            )
            return
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=buttons)

    async def _send_message_with_retry(
        context: ContextTypes.DEFAULT_TYPE,
        *,
        chat_id: int,
        text: str,
    ) -> Message | None:
        try:
            return await context.bot.send_message(chat_id=chat_id, text=text)
        except RetryAfter as exc:
            await asyncio.sleep(int(getattr(exc, "retry_after", 1)))
            try:
                return await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception:  # noqa: BLE001
                log.exception("send_message_failed chat=%s", chat_id)
                return None
        except Exception:  # noqa: BLE001
            log.exception("send_message_failed chat=%s", chat_id)
            return None

    async def _copy_message_with_retry(
        context: ContextTypes.DEFAULT_TYPE,
        *,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
        caption: str | None,
    ) -> Message | None:
        try:
            if caption is None:
                return await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
            return await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
                caption=caption,
            )
        except RetryAfter as exc:
            await asyncio.sleep(int(getattr(exc, "retry_after", 1)))
            try:
                if caption is None:
                    return await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=from_chat_id,
                        message_id=message_id,
                    )
                return await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                    caption=caption,
                )
            except Exception:  # noqa: BLE001
                log.exception("copy_message_failed chat=%s msg=%s", chat_id, message_id)
                return None
        except Exception:  # noqa: BLE001
            log.exception("copy_message_failed chat=%s msg=%s", chat_id, message_id)
            return None

    async def _delete_message_with_reason(
        *,
        chat_id: int,
        message_id: int,
        media_key: str,
        reason: str,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> bool:
        async with db_lock:
            db.add_pending_deletion(
                chat_id=chat_id,
                message_id=message_id,
                media_key=media_key,
                reason=reason,
            )
        dry_run, delete_enabled = await _effective_mode(chat_id)
        if dry_run or not delete_enabled:
            log.warning(
                "skip_delete reason=%s chat=%s msg=%s dry_run=%s delete_enabled=%s",
                reason,
                chat_id,
                message_id,
                int(dry_run),
                int(delete_enabled),
            )
            return False
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:  # noqa: BLE001
            async with db_lock:
                db.record_deletion_attempt(
                    chat_id=chat_id,
                    message_id=message_id,
                    media_key=media_key,
                    result="failed",
                    error=str(exc),
                )
            log.exception("delete_failed reason=%s chat=%s msg=%s", reason, chat_id, message_id)
            return False

        async with db_lock:
            db.record_deletion_attempt(
                chat_id=chat_id,
                message_id=message_id,
                media_key=media_key,
                result="success",
                error=None,
            )
            db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
        log.warning("deleted reason=%s chat=%s msg=%s", reason, chat_id, message_id)
        return True

    async def _copy_and_replace_messages(
        *,
        chat_id: int,
        messages: list[Any],
        caption_message_id: int | None,
        caption_text: str | None,
        apply_caption_to_all: bool = False,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> list[tuple[int, int]] | None:
        pairs: list[tuple[int, int]] = []
        new_message_ids: list[int] = []
        ordered = sorted(
            messages,
            key=lambda m: int(getattr(m, "id", 0) or getattr(m, "message_id", 0) or 0),
        )
        for msg in ordered:
            msg_id = int(getattr(msg, "id", 0) or getattr(msg, "message_id", 0) or 0)
            if msg_id <= 0:
                continue
            caption = None
            if caption_text is not None and (apply_caption_to_all or caption_message_id == msg_id):
                caption = caption_text
            new_msg = await _copy_message_with_retry(
                context,
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=msg_id,
                caption=caption,
            )
            if new_msg is None:
                for new_id in new_message_ids:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=new_id)
                    except Exception:  # noqa: BLE001
                        pass
                return None
            new_id = int(getattr(new_msg, "message_id", 0))
            if new_id <= 0:
                for new_id in new_message_ids:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=new_id)
                    except Exception:  # noqa: BLE001
                        pass
                return None
            new_message_ids.append(new_id)
            pairs.append((msg_id, new_id))

        for msg in ordered:
            msg_id = int(getattr(msg, "id", 0) or getattr(msg, "message_id", 0) or 0)
            if msg_id <= 0:
                continue
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:  # noqa: BLE001
                pass
        return pairs

    async def _sync_tag_aliases(chat_id: int) -> dict[str, str]:
        path = _tag_aliases_path(chat_id)
        global_mapping = _parse_tag_alias_file(_tag_aliases_global_path())
        file_mapping = _parse_tag_alias_file(path)
        # Global mapping wins on conflicts.
        merged_mapping = {**file_mapping, **global_mapping}
        async with db_lock:
            db_mapping = dict(db.list_tag_aliases(chat_id=chat_id))
            for old_tag, new_tag in merged_mapping.items():
                if db_mapping.get(old_tag) != new_tag:
                    db.set_tag_alias(chat_id=chat_id, old_tag=old_tag, new_tag=new_tag)
                    db.merge_tag_counts(chat_id=chat_id, old_tag=old_tag, new_tag=new_tag)
            for old_tag in db_mapping:
                if old_tag not in merged_mapping:
                    db.remove_tag_alias(chat_id=chat_id, old_tag=old_tag)
        return merged_mapping

    def _build_group_lines(
        *,
        tags_by_count: list[str],
        group_order: list[str],
        group_tags: dict[str, list[str]],
        max_tags: int,
    ) -> tuple[list[tuple[str, int]], int]:
        lines: list[tuple[str, int]] = []
        included = 0
        remaining = max_tags if max_tags > 0 else len(tags_by_count)
        tag_set = set(tags_by_count)
        for group in group_order:
            if remaining <= 0:
                break
            raw_tags = [tag for tag in group_tags.get(group, []) if tag in tag_set]
            if not raw_tags:
                continue
            if max_tags > 0:
                raw_tags = raw_tags[:remaining]
            if not raw_tags:
                continue
            lines.append((f"-------{group}--------", 0))
            tokens = [f"#{html.escape(tag)}" for tag in raw_tags]
            for line in _wrap_hashtags(tokens):
                lines.append((line, len(line.split())))
            included += len(raw_tags)
            remaining = max_tags - included if max_tags > 0 else remaining
        return lines, included

    def _sync_tag_groups(
        *,
        chat_id: int,
        alias_map: dict[str, str],
        tags_by_count: list[str],
    ) -> tuple[list[str], dict[str, list[str]]]:
        path = _tag_groups_path(chat_id)
        group_order, group_tags = _parse_tag_groups_file(path)
        changed = False

        for group in list(group_order):
            tags = group_tags.get(group, [])
            updated: list[str] = []
            for tag in tags:
                mapped = alias_map.get(tag, tag)
                if mapped != tag:
                    changed = True
                if mapped not in updated:
                    updated.append(mapped)
            group_tags[group] = updated

        known = {tag for tags in group_tags.values() for tag in tags}
        missing = [tag for tag in tags_by_count if tag not in known]
        if missing:
            if "其他" not in group_tags:
                group_order.append("其他")
                group_tags["其他"] = []
                changed = True
            for tag in missing:
                if tag not in group_tags["其他"]:
                    group_tags["其他"].append(tag)
                    changed = True

        if changed:
            _write_tag_groups_file(path, group_order, group_tags)
        return group_order, group_tags

    async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return False
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
        except Exception:  # noqa: BLE001
            return False
        return str(getattr(member, "status", "")).lower() in _BOT_MANAGE_STATUSES

    async def _ensure_private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        if str(getattr(chat, "type", "")).lower() == "private":
            return True
        await context.bot.send_message(chat_id=chat.id, text="请在私聊中使用该命令")
        return False

    async def _ensure_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return False
        async with db_lock:
            current = db.get_setting("controller_user_id")
            if current is None:
                db.set_setting("controller_user_id", str(int(user.id)))
                current = str(int(user.id))
        if int(current) != int(user.id):
            await context.bot.send_message(chat_id=chat.id, text=f"该 Bot 已绑定控制用户：{current}")
            return False
        return True

    async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        dry_run, delete_enabled = await _effective_mode(int(update.effective_chat.id))
        lines = [
            "🤖 频道管家已启动，欢迎使用！",
            f"版本：{__version__}",
            "使用 /help 查看完整指令列表。",
            "使用 /menu 打开主控菜单。",
        ]
        if web_panel_url:
            lines.append(f"Web 面板：{web_panel_url}")
        lines.append(f"当前：DRY_RUN={int(dry_run)} DELETE_DUPLICATES={int(delete_enabled)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="\n".join(lines),
        )
        await _send_main_menu(chat_id=int(update.effective_chat.id), context=context)

    async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        help_lines = [
            "🤖 频道管家使用指南",
            "",
            "🛠 管理指令（推荐 /menu）",
            "- /menu：按钮面板（补标签数量/媒体黑名单/标签更新/新建标签/删除特定媒体/批量删除/清理纯文字/媒体筛选）",
            "- /ping：健康检查",
            "- /stats：查看当前 chat 统计",
            "- /mode：查看/设置删除开关",
            "- /status：查看当前 chat 进行中的任务状态",
            "- /tags_pin [N] [MAX]：回溯提取 #标签，生成“标签目录”并置顶（标签过多会自动分多条置顶；需要 Telethon 用户账号 session；N=扫描条数，0=不限制；MAX=展示的唯一标签数上限，0=不限制）",
            "- /tags_pin <chat> [N] [MAX]：同上，显式指定目标（chat 支持 -100.../@username/邀请链接）",
            "- /tag_pin：/tags_pin 的别名",
            "- /tag_build：扫描历史媒体消息，按标签库出现次数优先匹配并补标签（最多 TAG_COUNT 个，默认 3，最大 10；限制条数由 TAG_BUILD_LIMIT 控制）",
            "- /tag_build_status：查看 tag_build 进度",
            "- /tag_build_stop：停止 tag_build 任务",
            "- /tag_rebuild [N|all]：历史消息标签替换+屏蔽文本删除（N=条数，从最新开始；all=全部，从最早开始）",
            "- /tag_update：标签更新（清理黑名单/屏蔽文本/补齐标签并生成目录）",
            "- /tag_stop：停止当前 chat 所有任务（scan/tags_pin/tag_build/tag_rebuild）",
            "- /tag_count [N]：设置每条消息最多补标签数（1-10；留空查看当前值）",
            "- /tag_rename [global] #旧=#新：设置标签别名（/tag_rename [global] list|del）",
            "- /text_block：管理屏蔽关键词（/text_block [global] list|add|del 关键词）",
            "- /scan [N]：回溯扫描当前群/频道历史（N=条数，0=不限制）",
            "- /scan <chat> [N]：显式指定目标（chat 支持 -100.../@username/邀请链接）",
            "- /scan_delete [N]：回溯扫描并删除重复（需先关闭 dry-run 并开启 delete）",
            "- /scan_delete <chat> [N]：同上，显式指定目标",
            "- /flush [N]：删除待删队列（默认 100，最大 1000）",
            "",
            "历史扫描说明：MTProto bot 账号无法拉取历史消息，需要 Telethon 用户账号 session。",
            "请私聊使用：/session_status /session_qr /session_reset /session_login /session_code /session_password /session_logout",
            "",
            "提示：群组里可用 /ping@你的Bot用户名 精确指向该 bot。",
        ]
        if web_panel_url:
            help_lines.extend(["", f"Web 面板：{web_panel_url}"])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="\n".join(help_lines),
        )

    async def _cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        await _send_main_menu(chat_id=chat_id, context=context)

    async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None or query.message is None:
            return
        chat = query.message.chat
        if chat is None:
            return
        chat_id = int(chat.id)
        if not _chat_allowed(config, chat_id):
            return

        data = str(query.data)
        parts = data.split(":")
        if not parts or parts[0] != "cm":
            return

        action = parts[1] if len(parts) > 1 else ""

        if action in {
            "set_tag_count",
            "toggle_media",
            "text_purge",
            "tag_update",
            "delete_by_text",
            "tag_add",
            "batch_delete",
        } or action.startswith("media_filter"):
            if not await _require_admin(update, context):
                await query.answer("需要管理员权限", show_alert=True)
                return

        if action == "menu":
            await query.answer()
            await _send_main_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "tag_count":
            await query.answer()
            current = await _effective_tag_count(chat_id)
            await query.edit_message_text(
                text=f"请选择补标签数量（当前 {current}）：",
                reply_markup=_tag_count_markup(current),
            )
            return

        if action == "set_tag_count":
            if len(parts) < 3 or not parts[2].isdigit():
                await query.answer("参数错误", show_alert=True)
                return
            value = int(parts[2])
            if value < 1 or value > 10:
                await query.answer("范围 1-10", show_alert=True)
                return
            async with db_lock:
                db.set_setting(_chat_setting_key(chat_id, "tag_count"), str(value))
            await query.answer()
            await _send_main_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "media_blacklist":
            await query.answer()
            selected = await _effective_media_blacklist(chat_id)
            await query.edit_message_text(
                text="请选择要屏蔽的媒体类型：",
                reply_markup=_media_blacklist_markup(selected),
            )
            return

        if action == "toggle_media":
            if len(parts) < 3:
                await query.answer("参数错误", show_alert=True)
                return
            media_type = parts[2].strip().lower()
            if media_type not in _MEDIA_BLACKLIST_TYPES:
                await query.answer("不支持的类型", show_alert=True)
                return
            async with db_lock:
                raw = db.get_setting(_media_blacklist_key(chat_id))
                selected = _parse_media_blacklist(raw)
                if media_type in selected:
                    selected.remove(media_type)
                else:
                    selected.add(media_type)
                db.set_setting(_media_blacklist_key(chat_id), _serialize_media_blacklist(selected))
            await query.answer()
            await query.edit_message_text(
                text="请选择要屏蔽的媒体类型：",
                reply_markup=_media_blacklist_markup(selected),
            )
            return

        if action == "text_purge":
            await query.answer()
            await _start_text_purge(
                chat_id=chat_id,
                chat_username=getattr(chat, "username", None),
                limit=0,
                context=context,
            )
            await query.edit_message_text(text="已开始清理纯文字消息。")
            return

        if action == "tag_update":
            await query.answer()
            await _start_tag_update(
                chat_id=chat_id,
                chat_username=getattr(chat, "username", None),
                context=context,
            )
            await query.edit_message_text(text="已开始标签更新流程。")
            return

        if action == "delete_by_text":
            delete_by_text_inputs[chat_id] = DeleteByTextInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_input",
            )
            await query.answer()
            await query.edit_message_text(
                text="请输入要删除的文本（不区分大小写，发送后需要确认）：",
                reply_markup=_delete_by_text_prompt_markup(),
            )
            return

        if action == "batch_delete":
            batch_delete_inputs[chat_id] = BatchDeleteInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_input",
            )
            await query.answer()
            await query.edit_message_text(
                text="请输入要删除的条数（1-1000，从最新开始）：",
                reply_markup=_batch_delete_prompt_markup(),
            )
            return

        if action == "tag_add":
            tag_add_inputs[chat_id] = TagAddInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_input",
            )
            await query.answer()
            await query.edit_message_text(
                text="请输入要添加的标签（示例：#男高 或 男高）：",
                reply_markup=_tag_add_prompt_markup(),
            )
            return

        if action == "media_filter":
            await query.answer()
            await _send_media_filter_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "media_filter_size":
            await query.answer()
            settings = _get_media_filter_settings(chat_id)
            await query.edit_message_text(
                text=f"设置媒体大小（单位 MB），当前：{_format_media_filter_size(settings)}",
                reply_markup=_media_filter_size_markup(),
            )
            return

        if action == "media_filter_size_clear":
            settings = _get_media_filter_settings(chat_id)
            settings.size_op = None
            settings.size_mb = None
            media_filter_inputs.pop(chat_id, None)
            await query.answer()
            await _send_media_filter_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "media_filter_size_op":
            if len(parts) < 3 or parts[2] not in {"gt", "lt"}:
                await query.answer("参数错误", show_alert=True)
                return
            media_filter_inputs[chat_id] = MediaFilterInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_size",
                pending_kind="size",
                pending_op=parts[2],
            )
            await query.answer()
            await query.edit_message_text(
                text=f"请输入媒体大小阈值（单位 MB，条件：{'大于' if parts[2] == 'gt' else '小于'}）：",
                reply_markup=_media_filter_input_cancel_markup(),
            )
            return

        if action == "media_filter_duration":
            await query.answer()
            settings = _get_media_filter_settings(chat_id)
            await query.edit_message_text(
                text=f"设置媒体时长（单位分钟），当前：{_format_media_filter_duration(settings)}",
                reply_markup=_media_filter_duration_markup(),
            )
            return

        if action == "media_filter_duration_clear":
            settings = _get_media_filter_settings(chat_id)
            settings.duration_op = None
            settings.duration_sec = None
            media_filter_inputs.pop(chat_id, None)
            await query.answer()
            await _send_media_filter_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "media_filter_duration_op":
            if len(parts) < 3 or parts[2] not in {"gt", "lt"}:
                await query.answer("参数错误", show_alert=True)
                return
            media_filter_inputs[chat_id] = MediaFilterInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_duration",
                pending_kind="duration",
                pending_op=parts[2],
            )
            await query.answer()
            await query.edit_message_text(
                text=f"请输入媒体时长阈值（单位分钟，条件：{'大于' if parts[2] == 'gt' else '小于'}）：",
                reply_markup=_media_filter_input_cancel_markup(),
            )
            return

        if action == "media_filter_type":
            await query.answer()
            settings = _get_media_filter_settings(chat_id)
            await query.edit_message_text(
                text=f"媒体类型筛选（当前：{_format_media_filter_type(settings)}）",
                reply_markup=_media_filter_type_markup(settings),
            )
            return

        if action == "media_filter_type_mode":
            if len(parts) < 3 or parts[2] not in {"off", "blacklist", "whitelist"}:
                await query.answer("参数错误", show_alert=True)
                return
            settings = _get_media_filter_settings(chat_id)
            settings.type_mode = parts[2]
            await query.answer()
            await query.edit_message_text(
                text=f"媒体类型筛选（当前：{_format_media_filter_type(settings)}）",
                reply_markup=_media_filter_type_markup(settings),
            )
            return

        if action == "media_filter_type_toggle":
            if len(parts) < 3:
                await query.answer("参数错误", show_alert=True)
                return
            media_type = parts[2].strip().lower()
            if media_type not in _MEDIA_BLACKLIST_TYPES:
                await query.answer("不支持的类型", show_alert=True)
                return
            settings = _get_media_filter_settings(chat_id)
            if media_type in settings.type_set:
                settings.type_set.remove(media_type)
            else:
                settings.type_set.add(media_type)
            await query.answer()
            await query.edit_message_text(
                text=f"媒体类型筛选（当前：{_format_media_filter_type(settings)}）",
                reply_markup=_media_filter_type_markup(settings),
            )
            return

        if action == "media_filter_text_toggle":
            settings = _get_media_filter_settings(chat_id)
            settings.include_text = not settings.include_text
            await query.answer()
            await _send_media_filter_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "media_filter_start":
            settings = _get_media_filter_settings(chat_id)
            media_filter_inputs[chat_id] = MediaFilterInput(
                user_id=int(getattr(query.from_user, "id", 0) or 0),
                step="await_confirm",
                snapshot=_clone_media_filter_settings(settings),
            )
            await query.answer()
            await query.edit_message_text(
                text=(
                    "确认开始媒体筛选？\n"
                    f"{_format_media_filter_summary(settings)}\n\n"
                    "说明：大小/时长仅在可获取时判断，缺失则跳过该条件。"
                ),
                reply_markup=_media_filter_confirm_markup(),
            )
            return

        if action == "media_filter_confirm":
            pending = media_filter_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人确认", show_alert=True)
                return
            if pending is None or pending.snapshot is None:
                await query.answer("请先设置筛选条件", show_alert=True)
                return
            if chat_id in media_filter_tasks and not media_filter_tasks[chat_id].done():
                await query.answer("已有筛选任务在运行", show_alert=True)
                return
            media_filter_inputs.pop(chat_id, None)
            await query.answer()
            await _start_media_filter(
                chat_id=chat_id,
                chat_username=getattr(chat, "username", None),
                settings=pending.snapshot,
                context=context,
            )
            await query.edit_message_text(text="已开始媒体筛选。")
            return

        if action == "media_filter_cancel":
            pending = media_filter_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人取消", show_alert=True)
                return
            media_filter_inputs.pop(chat_id, None)
            await query.answer()
            await _send_media_filter_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "delete_by_text_confirm":
            pending = delete_by_text_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人确认", show_alert=True)
                return
            if pending is None or not pending.keyword:
                await query.answer("请先输入要删除的文本", show_alert=True)
                return
            delete_by_text_inputs.pop(chat_id, None)
            await query.answer()
            await _start_delete_by_text(
                chat_id=chat_id,
                chat_username=getattr(chat, "username", None),
                keyword=pending.keyword,
                context=context,
            )
            await query.edit_message_text(text="已开始删除包含指定文本的消息。")
            return

        if action == "batch_delete_confirm":
            pending = batch_delete_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人确认", show_alert=True)
                return
            if pending is None or pending.count is None:
                await query.answer("请先输入要删除的条数", show_alert=True)
                return
            limit = int(pending.count)
            batch_delete_inputs.pop(chat_id, None)
            await query.answer()
            await _start_batch_delete(
                chat_id=chat_id,
                chat_username=getattr(chat, "username", None),
                limit=limit,
                context=context,
            )
            await query.edit_message_text(text=f"已开始批量删除最新 {limit} 条消息。")
            return

        if action == "batch_delete_cancel":
            pending = batch_delete_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人取消", show_alert=True)
                return
            batch_delete_inputs.pop(chat_id, None)
            await query.answer()
            await _send_main_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "delete_by_text_cancel":
            pending = delete_by_text_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人取消", show_alert=True)
                return
            delete_by_text_inputs.pop(chat_id, None)
            await query.answer()
            await _send_main_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "tag_add_cancel":
            pending = tag_add_inputs.get(chat_id)
            if pending is not None and int(getattr(query.from_user, "id", 0) or 0) != int(pending.user_id):
                await query.answer("仅限发起人取消", show_alert=True)
                return
            tag_add_inputs.pop(chat_id, None)
            await query.answer()
            await _send_main_menu(chat_id=chat_id, context=context, edit_message_id=query.message.message_id)
            return

        if action == "close":
            await query.answer()
            await query.edit_message_text(text="已关闭。")
            return

    def _telethon_error_text(exc: Exception) -> str:
        name = exc.__class__.__name__
        raw = str(exc)
        if "database is locked" in raw.lower():
            return "SQLite 数据库正被占用（database is locked）：请确认只运行了一个 bot 实例/扫描进程，稍后重试；必要时重启 bot"
        if "bot users is restricted" in raw.lower():
            return "当前 Telethon 会话被识别为 bot，无法执行该操作；请先 /session_reset 再重新 /session_login"
        if name == "FloodWaitError":
            seconds = getattr(exc, "seconds", None)
            return f"触发限流，请等待 {seconds}s 后重试" if seconds is not None else "触发限流，请稍后重试"
        if name in {"PhoneNumberInvalidError"}:
            return "手机号格式无效"
        if name in {"PhoneCodeInvalidError"}:
            return "验证码错误"
        if name in {"PhoneCodeExpiredError"}:
            return (
                "验证码已过期或已被 Telegram 风控作废。"
                "若你曾把验证码发送到任何聊天/机器人（包括把数字发给本 bot），Telegram 可能会拦截登录；"
                "建议改用 /session_qr 扫码登录"
            )
        if name in {"SessionPasswordNeededError"}:
            return "该账号开启了两步验证密码，请使用 /session_password"
        if name in {"PasswordHashInvalidError"}:
            return "两步验证密码错误"
        return str(exc)

    def _build_tag_directory_texts(
        *,
        result: TagScanResult,
        max_tags: int,
        generated_at: int,
        display_lines: list[tuple[str, int]] | None = None,
    ) -> tuple[list[str], int]:
        max_tags = max(0, int(max_tags))
        tags_all = sorted(result.tag_counts.keys(), key=lambda t: (t.casefold(), t))

        total_unique = len(tags_all)
        generated_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(generated_at))
        limited_suffix = f"（MAX：{max_tags}）" if 0 < max_tags < total_unique else ""

        max_hashtags_per_message = 80

        line_items: list[tuple[str, int]]
        if display_lines is not None:
            line_items = display_lines
            shown_unique = sum(count for _, count in line_items)
        else:
            tags = tags_all if max_tags <= 0 else tags_all[:max_tags]
            shown_unique = len(tags)
            tag_tokens = [f"#{html.escape(tag)}" for tag in tags]
            tag_lines = _wrap_hashtags(tag_tokens)
            line_items = [(line, len(line.split())) for line in tag_lines]

        if shown_unique <= 0:
            text = "\n".join(
                [
                    "<b>标签目录</b>",
                    "点击任意标签可快速搜索",
                    "",
                    f"• 扫描消息：{result.scanned}",
                    f"• 唯一标签：{total_unique}",
                    f"• 标签总出现次数：{result.total_tags}",
                    f"• 生成时间：{html.escape(generated_text)}",
                    "",
                    "未发现任何标签",
                    "",
                    "重新生成：<code>/tags_pin [N] [MAX]</code>",
                ]
            )
            return [text], 0

        texts: list[str] = []
        line_idx = 0
        part = 1
        max_parts = 100
        displayed_unique = 0
        displayed_placeholder = "DISPLAYEDCOUNTPLACEHOLDER"

        while line_idx < len(line_items) and len(texts) < max_parts:
            if part == 1:
                header = "\n".join(
                    [
                        "<b>标签目录</b>",
                        "点击任意标签可快速搜索",
                        "",
                        f"• 扫描消息：{result.scanned}",
                        f"• 唯一标签：{total_unique}",
                        f"• 展示：{displayed_placeholder}/{total_unique}{limited_suffix}",
                        f"• 生成时间：{html.escape(generated_text)}",
                    ]
                )
                footer = "\n\n重新生成：<code>/tags_pin [N] [MAX]</code>"
            else:
                header = f"<b>标签目录（第{part}部分）</b>"
                footer = ""

            available = 4096 - len(header) - len(footer) - 2
            body_lines: list[str] = []
            tags_in_part = 0

            while line_idx < len(line_items):
                line, line_tag_count = line_items[line_idx]
                extra = len(line) + (1 if body_lines else 0)
                if extra > available:
                    break
                if body_lines and tags_in_part + line_tag_count > max_hashtags_per_message:
                    break
                body_lines.append(line)
                tags_in_part += line_tag_count
                displayed_unique += line_tag_count
                available -= extra
                line_idx += 1

            if not body_lines:
                line, line_tag_count = line_items[line_idx]
                body_lines.append(line[: max(0, available)])
                displayed_unique += line_tag_count
                line_idx += 1

            text = f"{header}\n\n" + "\n".join(body_lines) + footer
            texts.append(text)
            part += 1

        if texts:
            texts[0] = texts[0].replace(displayed_placeholder, str(displayed_unique), 1)

        if texts and line_idx < len(line_items):
            omitted = max(0, shown_unique - displayed_unique)
            note = f"（标签过多，剩余约 {omitted} 个未展示；可用 /tags_pin [N] [MAX] 限制输出）"
            if len(texts[0]) + 2 + len(note) <= 4096:
                texts[0] += "\n\n" + note
            else:
                short_note = f"（剩余约 {omitted} 个未展示）"
                if len(texts[0]) + 2 + len(short_note) <= 4096:
                    texts[0] += "\n\n" + short_note

        return texts, displayed_unique

    async def _start_tags_pin(
        *,
        chat_id: int,
        chat_username: str | None,
        limit: int,
        max_tags: int,
        context: ContextTypes.DEFAULT_TYPE,
        target_chat: str | None,
    ) -> None:
        if chat_id in tag_tasks and not tag_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有标签目录任务在运行，请稍后再试")
            return

        state = TagSummaryState(started_at=int(time.time()), limit=limit, max_tags=max_tags)
        tag_states[chat_id] = state

        msg = await context.bot.send_message(chat_id=chat_id, text="开始生成标签目录：准备中…")
        state.progress_message_id = int(msg.message_id)

        async def progress_cb(p: TagScanProgress) -> None:
            state.scanned = p.scanned
            state.unique_tags = p.unique_tags
            state.total_tags = p.total_tags
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"生成中：scanned={state.scanned} unique_tags={state.unique_tags} "
                        f"total_tags={state.total_tags} limit={limit} max_tags={max_tags}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            pin_error: str | None = None
            try:
                async with telethon_lock:
                    result = await run_tag_scan(
                        chat=target_chat,
                        bot_chat_id=chat_id,
                        bot_chat_username=chat_username,
                        limit=limit,
                        reverse=True,
                        interactive=False,
                        progress_cb=progress_cb,
                    )

                alias_map = await _sync_tag_aliases(chat_id)
                tag_counts = _apply_tag_aliases_to_counts(result.tag_counts, alias_map)
                result = TagScanResult(
                    scanned=result.scanned,
                    tag_counts=tag_counts,
                    total_tags=sum(tag_counts.values()),
                )
                tags_by_count = sorted(tag_counts.keys(), key=lambda t: (-tag_counts[t], t))
                group_order, group_tags = _sync_tag_groups(
                    chat_id=chat_id,
                    alias_map=alias_map,
                    tags_by_count=tags_by_count,
                )
                display_lines, _ = _build_group_lines(
                    tags_by_count=tags_by_count,
                    group_order=group_order,
                    group_tags=group_tags,
                    max_tags=max_tags,
                )

                state.running = False
                summary_texts, shown_unique = _build_tag_directory_texts(
                    result=result,
                    max_tags=max_tags,
                    generated_at=int(time.time()),
                    display_lines=display_lines,
                )

                sent_messages: list[Message] = []
                for text in summary_texts:
                    sent_messages.append(
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                        )
                    )

                pinned_ids: list[int] = []
                pin_errors: list[str] = []
                for msg in reversed(sent_messages):
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=chat_id,
                            message_id=int(msg.message_id),
                            disable_notification=True,
                        )
                        pinned_ids.append(int(msg.message_id))
                    except Exception as exc:  # noqa: BLE001
                        pin_errors.append(_telethon_error_text(exc))

                if pin_errors:
                    pin_error = "\n".join(pin_errors[:3])

                if pinned_ids:
                    key = _chat_setting_key(chat_id, "tags_pin_pinned_ids")
                    async with db_lock:
                        old_raw = db.get_setting(key) or ""
                    old_ids = [int(x) for x in old_raw.split(",") if x.strip().isdigit()]
                    for old_id in old_ids:
                        if old_id in pinned_ids:
                            continue
                        try:
                            await context.bot.unpin_chat_message(chat_id=chat_id, message_id=old_id)
                        except Exception:  # noqa: BLE001
                            pass
                    async with db_lock:
                        db.set_setting(key, ",".join(str(i) for i in sorted(set(pinned_ids))))

                done = (
                    f"标签目录已生成：scanned={result.scanned} unique_tags={len(result.tag_counts)} "
                    f"total_tags={result.total_tags} shown_unique={shown_unique} parts={len(summary_texts)}"
                )
                if pin_error:
                    done += f"\n置顶失败：{pin_error}"
                else:
                    done += "\n已置顶目录消息"
                await finalize(done)
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    f"标签目录生成已取消：scanned={state.scanned} unique_tags={state.unique_tags} total_tags={state.total_tags}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    f"标签目录生成失败：{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} unique_tags={state.unique_tags} total_tags={state.total_tags}"
                )
            finally:
                tag_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            tag_tasks[chat_id] = context.application.create_task(runner())
        else:
            tag_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_session_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return
        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                authorized = await client.is_user_authorized()
                if not authorized:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"未授权（session={config.tg_session}）。请执行 /session_login +8613xxxx",
                    )
                    return
                me = await client.get_me()
            finally:
                await client.disconnect()

        if getattr(me, "bot", False):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"已授权但为 bot 账号（session={config.tg_session}），无法用于历史扫描/发送验证码。\n"
                    "请执行 /session_reset 后用用户账号重新 /session_login"
                ),
            )
            return

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"已授权：type=user id={getattr(me, 'id', None)} "
                f"username=@{getattr(me, 'username', None)} session={config.tg_session}"
            ),
        )

    async def _cmd_session_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if update.effective_user is None:
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_login +8613xxxx")
            return

        phone = context.args[0].strip()
        if not phone:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_login +8613xxxx")
            return

        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                authorized = await client.is_user_authorized()
                if authorized:
                    me = await client.get_me()
                    if getattr(me, "bot", False):
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=(
                                f"当前 session 已授权为 bot 账号（session={config.tg_session}），无法发送验证码。\n"
                                "请执行 /session_reset 后重试 /session_login"
                            ),
                        )
                        return
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=(
                            f"当前 session 已授权为用户账号（@{getattr(me, 'username', None)}），无需重复登录。\n"
                            "可直接在群内执行 /scan"
                        ),
                    )
                    return
                sent = await client.send_code_request(phone)
            except Exception as exc:  # noqa: BLE001
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"发送验证码失败：{_telethon_error_text(exc)}")
                return
            finally:
                await client.disconnect()

        login_states[int(update.effective_user.id)] = PendingLogin(
            phone=phone,
            phone_code_hash=str(getattr(sent, "phone_code_hash", "")),
            started_at=int(time.time()),
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "验证码已发送。\n"
                "注意：把验证码发送到任何聊天/机器人可能触发 Telegram 风控拦截登录；推荐直接用 /session_qr 扫码登录。\n"
                "如仍要用验证码方式，再执行 /session_code 12345（如有两步验证会提示再 /session_password）"
            ),
        )

    async def _cmd_session_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if update.effective_chat is None or update.effective_user is None:
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return

        user_id = int(update.effective_user.id)
        chat_id = int(update.effective_chat.id)

        existing = qr_tasks.get(user_id)
        if existing is not None and not existing.done():
            await context.bot.send_message(chat_id=chat_id, text="已有二维码登录流程在运行，请先完成扫码或稍后重试")
            return

        login_states.pop(user_id, None)

        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=chat_id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async def runner() -> None:
            try:
                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        if await client.is_user_authorized():
                            me = await client.get_me()
                            if getattr(me, "bot", False):
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=(
                                        f"当前 session 已授权为 bot 账号（session={config.tg_session}），无法用于历史扫描。\n"
                                        "请先 /session_reset 再重新授权"
                                    ),
                                )
                                return
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"已授权：@{getattr(me, 'username', None)}（session={config.tg_session}），可直接在群内 /scan",
                            )
                            return

                        if not hasattr(client, "qr_login"):
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="当前 Telethon 版本不支持二维码登录，请使用 /session_login（验证码方式）或升级 Telethon",
                            )
                            return

                        qr = await client.qr_login()  # type: ignore[attr-defined]
                        url = str(getattr(qr, "url", "")).strip() or None
                        if not url:
                            await context.bot.send_message(chat_id=chat_id, text="生成二维码失败：缺少登录 URL")
                            return

                        try:
                            import qrcode  # type: ignore
                            from io import BytesIO

                            img = qrcode.make(url)
                            bio = BytesIO()
                            img.save(bio, format="PNG")
                            bio.seek(0)
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=bio,
                                caption=(
                                    "请在 3 分钟内用手机 Telegram 扫码登录：\n"
                                    "设置 → 设备 → 关联桌面设备/扫描二维码。\n"
                                    "扫码后稍等，我会提示登录结果。"
                                ),
                            )
                        except Exception:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "未安装二维码生成依赖，无法直接发送图片。\n"
                                    "请把下面 URL 转成二维码后扫描（或安装 qrcode+pillow 后重试）：\n"
                                    f"{url}\n"
                                    "提示：扫码链接请勿分享，等同于登录凭证。"
                                ),
                            )

                        try:
                            wait = getattr(qr, "wait", None)
                            if not callable(wait):
                                await context.bot.send_message(chat_id=chat_id, text="二维码登录失败：Telethon 接口异常")
                                return
                            await asyncio.wait_for(wait(), timeout=180)
                        except asyncio.TimeoutError:
                            await context.bot.send_message(chat_id=chat_id, text="二维码登录超时，请重新 /session_qr")
                            return
                        except Exception as exc:  # noqa: BLE001
                            if exc.__class__.__name__ == "SessionPasswordNeededError":
                                login_states[user_id] = PendingLogin(
                                    phone="",
                                    phone_code_hash="",
                                    started_at=int(time.time()),
                                    needs_password=True,
                                )
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text="该账号开启了两步验证密码，请执行 /session_password 你的两步验证密码",
                                )
                                return
                            raise

                        if not await client.is_user_authorized():
                            await context.bot.send_message(chat_id=chat_id, text="二维码登录未完成，请重试 /session_qr")
                            return

                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text="登录结果异常：当前 session 为 bot 账号。请 /session_reset 后重试",
                            )
                            return

                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"登录成功：id={getattr(me, 'id', None)} username=@{getattr(me, 'username', None)}",
                        )
                    finally:
                        await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                await context.bot.send_message(chat_id=chat_id, text=f"二维码登录失败：{_telethon_error_text(exc)}")
            finally:
                qr_tasks.pop(user_id, None)

        if hasattr(context.application, "create_task"):
            qr_tasks[user_id] = context.application.create_task(runner())
        else:
            qr_tasks[user_id] = asyncio.create_task(runner())

    async def _cmd_session_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if update.effective_user is None:
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return
        state = login_states.get(int(update.effective_user.id))
        if state is None:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="请先 /session_login 发送验证码")
            return
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_code 12345")
            return
        code = context.args[0].strip()
        if not code:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_code 12345")
            return

        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                try:
                    await client.sign_in(phone=state.phone, code=code, phone_code_hash=state.phone_code_hash)
                except Exception as exc:  # noqa: BLE001
                    msg = _telethon_error_text(exc)
                    if exc.__class__.__name__ == "SessionPasswordNeededError":
                        login_states[int(update.effective_user.id)].needs_password = True
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"登录失败：{msg}")
                    return

                authorized = await client.is_user_authorized()
                if not authorized:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="登录未完成，请重试 /session_code 或 /session_password")
                    return
                me = await client.get_me()
            finally:
                await client.disconnect()

        login_states.pop(int(update.effective_user.id), None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"登录成功：id={getattr(me, 'id', None)} username=@{getattr(me, 'username', None)}",
        )

    async def _cmd_session_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if update.effective_user is None:
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return
        state = login_states.get(int(update.effective_user.id))
        if state is None or not state.needs_password:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="当前不需要两步验证密码；如未登录请先 /session_login")
            return
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_password 你的两步验证密码")
            return
        password = " ".join(context.args).strip()
        if not password:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="用法：/session_password 你的两步验证密码")
            return

        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                try:
                    await client.sign_in(password=password)
                except Exception as exc:  # noqa: BLE001
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"登录失败：{_telethon_error_text(exc)}")
                    return
                me = await client.get_me()
            finally:
                await client.disconnect()

        login_states.pop(int(update.effective_user.id), None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"登录成功：id={getattr(me, 'id', None)} username=@{getattr(me, 'username', None)}",
        )

    async def _cmd_session_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return
        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                try:
                    await client.log_out()
                except Exception as exc:  # noqa: BLE001
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"退出失败：{_telethon_error_text(exc)}")
                    return
            finally:
                await client.disconnect()

        login_states.pop(int(update.effective_user.id), None)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已退出登录（session 已失效）")

    def _session_file_paths() -> list[Path]:
        session_str = str(config.tg_session).strip()
        if not session_str:
            return []
        session_path = Path(session_str).expanduser()
        if session_path.suffix != ".session":
            session_path = Path(session_str + ".session").expanduser()
        if not session_path.is_absolute():
            session_path = (Path.cwd() / session_path).resolve()
        else:
            session_path = session_path.resolve()
        return [
            session_path,
            Path(str(session_path) + "-journal"),
            Path(str(session_path) + "-wal"),
            Path(str(session_path) + "-shm"),
        ]

    async def _cmd_session_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_private_chat(update, context):
            return
        if not await _ensure_controller(update, context):
            return
        if update.effective_chat is None:
            return
        if config.tg_api_id is None or not config.tg_api_hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="缺少 TG_API_ID/TG_API_HASH")
            return

        paths = _session_file_paths()
        if not paths:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="TG_SESSION 为空，无法重置")
            return

        cwd = Path.cwd().resolve()
        try:
            paths[0].relative_to(cwd)
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"出于安全限制，无法自动删除工作目录外的 session：{paths[0]}",
            )
            return

        try:
            from telethon import TelegramClient  # type: ignore
        except Exception:  # noqa: BLE001
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt",
            )
            return

        async with telethon_lock:
            client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
            await client.connect()
            try:
                if await client.is_user_authorized():
                    try:
                        await client.log_out()
                    except Exception:
                        pass
            finally:
                await client.disconnect()

        removed: list[str] = []
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            removed.append(str(path))

        login_states.pop(int(update.effective_user.id), None)

        if not removed:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"已重置：未找到本地 session 文件（预期路径：{paths[0]}）",
            )
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"已重置 session，已删除 {len(removed)} 个文件。请重新执行 /session_login +8613xxxx",
        )

    async def _cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        dry_run, delete_enabled = await _effective_mode(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"模式（chat={chat_id}）：DRY_RUN={int(dry_run)} DELETE_DUPLICATES={int(delete_enabled)}\n"
                "管理员可用：/dry_run_on /dry_run_off /enable_delete /disable_delete"
            ),
        )

    async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return

        lines: list[str] = []

        scan_state = scan_states.get(chat_id)
        if scan_state and chat_id in scan_tasks:
            lines.append(
                "scan: "
                f"scanned={scan_state.scanned} dup={scan_state.decided_delete} "
                f"deleted={scan_state.deleted} failed={scan_state.failed} limit={scan_state.limit}"
            )

        tag_state = tag_states.get(chat_id)
        if tag_state and chat_id in tag_tasks:
            lines.append(
                "tags_pin: "
                f"scanned={tag_state.scanned} unique_tags={tag_state.unique_tags} "
                f"total_tags={tag_state.total_tags} limit={tag_state.limit} max_tags={tag_state.max_tags}"
            )

        tag_build_state = tag_build_states.get(chat_id)
        if tag_build_state and chat_id in tag_build_tasks:
            lines.append(
                "tag_build: "
                f"scanned={tag_build_state.scanned} tagged={tag_build_state.tagged} "
                f"skipped={tag_build_state.skipped} failed={tag_build_state.failed} limit={tag_build_state.limit}"
            )

        tag_rebuild_state = tag_rebuild_states.get(chat_id)
        if tag_rebuild_state and chat_id in tag_rebuild_tasks:
            lines.append(
                "tag_rebuild: "
                f"scanned={tag_rebuild_state.scanned} rebuilt={tag_rebuild_state.rebuilt} "
                f"skipped={tag_rebuild_state.skipped} failed={tag_rebuild_state.failed} limit={tag_rebuild_state.limit}"
            )

        if not lines:
            await context.bot.send_message(chat_id=chat_id, text="当前无进行中的任务")
            return
        await context.bot.send_message(chat_id=chat_id, text="进行中的任务：\n" + "\n".join(lines))

    async def _cmd_enable_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        async with db_lock:
            db.set_setting(_chat_setting_key(chat_id, "delete_duplicates"), "1")
        await context.bot.send_message(chat_id=chat_id, text="已开启 DELETE_DUPLICATES（仍可能受 DRY_RUN 影响）")

    async def _cmd_disable_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        async with db_lock:
            db.set_setting(_chat_setting_key(chat_id, "delete_duplicates"), "0")
        await context.bot.send_message(chat_id=chat_id, text="已关闭 DELETE_DUPLICATES")

    async def _cmd_dry_run_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        async with db_lock:
            db.set_setting(_chat_setting_key(chat_id, "dry_run"), "1")
        await context.bot.send_message(chat_id=chat_id, text="已开启 DRY_RUN（只记录不删除）")

    async def _cmd_dry_run_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        async with db_lock:
            db.set_setting(_chat_setting_key(chat_id, "dry_run"), "0")
        await context.bot.send_message(chat_id=chat_id, text="已关闭 DRY_RUN（允许删除，仍需开启 DELETE_DUPLICATES）")

    async def _cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return

        async with db_lock:
            stats = db.get_chat_stats(chat_id)

        dry_run, delete_enabled = await _effective_mode(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"统计（chat={chat_id}）：\n"
                f"- 已处理媒体消息：{stats['media_messages']}\n"
                f"- 唯一媒体：{stats['unique_media']}\n"
                f"- 判定重复：{stats['duplicates_found']}\n"
                f"- 待删队列：{stats['pending_deletions']}\n"
                f"- 删除成功/失败：{stats['deleted_success']}/{stats['deleted_failed']}\n"
                f"模式：DRY_RUN={int(dry_run)} DELETE_DUPLICATES={int(delete_enabled)}"
            ),
        )

    async def _maybe_tag_build_message(
        *,
        message: Message,
        item: MediaItem,
        decision: ProcessDecision,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        caption = message.caption or ""
        if not caption:
            return
        if decision.message_id_to_delete == item.message_id:
            return
        async with db_lock:
            if db.is_tag_build_sent(chat_id=item.chat_id, message_id=int(message.message_id)):
                return

        alias_map = await _sync_tag_aliases(item.chat_id)
        tags = _extract_hashtags_bot(caption, message.caption_entities)
        sender_id = getattr(message.from_user, "id", None)
        mapped: list[str] = []
        if tags:
            mapped = _apply_tag_aliases(tags, alias_map)
            if sender_id is None or int(sender_id) != int(context.bot.id):
                async with db_lock:
                    db.record_tags(chat_id=item.chat_id, tags=mapped)

        async with db_lock:
            tag_counts = db.list_tag_counts(chat_id=item.chat_id)
            db_keywords = db.list_text_block_keywords()
            raw_tag_count = db.get_setting(_chat_setting_key(item.chat_id, "tag_count"))

        block_keywords = _collect_text_block_keywords(chat_id=item.chat_id, db_keywords=db_keywords)
        tag_count = _parse_int_setting(raw_tag_count, min_value=1, max_value=10) or config.tag_count
        base_text = _strip_hashtags(caption)
        cleaned = _apply_text_block(base_text, block_keywords)
        needed = max(tag_count - len(mapped), 0)
        if needed <= 0:
            return
        matched = _match_tags_excluding(cleaned, tag_counts, max_tags=needed, exclude=mapped)
        if not matched:
            return

        new_tags = [*mapped, *matched]
        new_caption = _build_tag_caption(cleaned, new_tags)

        new_msg = await _copy_message_with_retry(
            context,
            chat_id=item.chat_id,
            from_chat_id=item.chat_id,
            message_id=item.message_id,
            caption=new_caption,
        )
        if new_msg is None:
            log.error("tag_build_copy_failed chat=%s msg=%s", item.chat_id, item.message_id)
            return
        if new_msg is None:
            return
        new_msg_id = int(getattr(new_msg, "message_id", 0))
        if new_msg_id <= 0:
            return

        try:
            await context.bot.delete_message(chat_id=item.chat_id, message_id=item.message_id)
        except Exception:  # noqa: BLE001
            log.exception("tag_build_delete_failed chat=%s msg=%s", item.chat_id, item.message_id)

        async with db_lock:
            db.add_tag_build_sent(chat_id=item.chat_id, message_id=new_msg_id)
            db.replace_message_id(
                chat_id=item.chat_id,
                old_message_id=item.message_id,
                new_message_id=new_msg_id,
            )

    async def _cmd_text_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        args = [arg.strip() for arg in context.args if arg.strip()]
        scope = "chat"
        if args and args[0].lower() in {"global", "all"}:
            scope = "global"
            args = args[1:]

        action = args[0].lower() if args else "list"

        async with db_lock:
            db_keywords = db.list_text_block_keywords()
        global_keywords = _merge_text_block_keywords(db_keywords, _parse_text_block_file(_text_block_global_path()))
        chat_keywords = _parse_text_block_file(_text_block_chat_path(chat_id))

        def _render_list(label: str, keywords: list[str]) -> str:
            if not keywords:
                return f"{label}为空"
            preview = "\n".join(f"{idx + 1}. {kw}" for idx, kw in enumerate(keywords[:50]))
            suffix = "\n（仅展示前 50 条）" if len(keywords) > 50 else ""
            return f"{label}：\n{preview}{suffix}"

        if action in {"list", "ls"}:
            if scope == "global":
                text = _render_list("text_block(全局)", global_keywords)
            else:
                text = "\n\n".join(
                    [
                        _render_list("text_block(全局)", global_keywords),
                        _render_list(f"text_block(群组 {chat_id})", chat_keywords),
                    ]
                )
            await context.bot.send_message(chat_id=chat_id, text=text)
            return

        target_path = _text_block_global_path() if scope == "global" else _text_block_chat_path(chat_id)
        action_args = args[1:] if args else []

        if action in {"add", "set"}:
            keyword = " ".join(action_args).strip()
            if not keyword:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="用法：/text_block [global] list|add|del 关键词",
                )
                return
            keywords = _parse_text_block_file(target_path)
            normalized = keyword.casefold()
            if all(kw.casefold() != normalized for kw in keywords):
                keywords.append(keyword)
                _write_text_block_file(target_path, keywords)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"已添加 text_block（{scope}）：{keyword.strip()}",
            )
            return

        if action in {"del", "remove", "rm"}:
            keyword = " ".join(action_args).strip()
            if not keyword:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="用法：/text_block [global] list|add|del 关键词",
                )
                return
            keywords = _parse_text_block_file(target_path)
            normalized = keyword.casefold()
            filtered = [kw for kw in keywords if kw.casefold() != normalized]
            if len(filtered) == len(keywords):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"未找到 text_block（{scope}）：{keyword.strip()}",
                )
                return
            _write_text_block_file(target_path, filtered)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"已移除 text_block（{scope}）：{keyword.strip()}",
            )
            return

        keyword = " ".join(args).strip()
        if not keyword:
            await context.bot.send_message(
                chat_id=chat_id,
                text="用法：/text_block [global] list|add|del 关键词",
            )
            return
        keywords = _parse_text_block_file(target_path)
        normalized = keyword.casefold()
        if all(kw.casefold() != normalized for kw in keywords):
            keywords.append(keyword)
            _write_text_block_file(target_path, keywords)
        await context.bot.send_message(chat_id=chat_id, text=f"已添加 text_block（{scope}）：{keyword.strip()}")

    async def _start_tag_build(
        *,
        chat_id: int,
        chat_username: str | None,
        limit: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in tag_build_tasks and not tag_build_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有 tag_build 任务在运行，请稍后再试")
            return

        state = TagBuildState(started_at=int(time.time()), limit=limit, last_progress_at=int(time.time()))
        tag_build_states[chat_id] = state

        start_text = f"开始 tag_build：target={chat_username or chat_id} limit={limit}"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"tag_build 进行中：scanned={state.scanned} tagged={state.tagged} "
                        f"skipped={state.skipped} failed={state.failed} limit={limit}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("tag_build 需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行 tag_build")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        alias_map = await _sync_tag_aliases(chat_id)
                        async with db_lock:
                            tag_counts = {tag: count for tag, count in db.list_tag_counts(chat_id=chat_id)}
                            db_keywords = db.list_text_block_keywords()
                            raw_tag_count = db.get_setting(_chat_setting_key(chat_id, "tag_count"))
                            raw_blacklist = db.get_setting(_media_blacklist_key(chat_id))

                        block_keywords = _collect_text_block_keywords(chat_id=chat_id, db_keywords=db_keywords)
                        tag_count = _parse_int_setting(raw_tag_count, min_value=1, max_value=10) or config.tag_count
                        blacklist = _parse_media_blacklist(raw_blacklist)
                        tag_counts_list = sorted(tag_counts.items(), key=lambda t: (-t[1], t[0]))
                        tag_counts_dirty = False
                        bot_id = int(context.bot.id)

                        async def handle_group(messages: list[Any]) -> None:
                            nonlocal tag_counts_dirty, tag_counts_list
                            media_msgs = [
                                msg
                                for msg in messages
                                if getattr(msg, "photo", None) is not None or getattr(msg, "document", None) is not None
                            ]
                            if not media_msgs:
                                state.skipped += len(messages)
                                return

                            if blacklist:
                                blocked_msgs: list[tuple[Any, str]] = []
                                for msg in media_msgs:
                                    media_type = _telethon_media_type(msg)
                                    if media_type and media_type in blacklist:
                                        blocked_msgs.append((msg, media_type))
                                if blocked_msgs:
                                    for msg, media_type in blocked_msgs:
                                        await _delete_message_with_reason(
                                            chat_id=chat_id,
                                            message_id=int(getattr(msg, "id", 0) or 0),
                                            media_key=f"mtproto:{media_type}:{getattr(msg, 'id', 0)}",
                                            reason=f"media_blacklist:{media_type}",
                                            context=context,
                                        )
                                    state.skipped += len(media_msgs)
                                    return

                            caption_msg = None
                            caption_text = ""
                            for msg in media_msgs:
                                text = str(getattr(msg, "message", "") or "")
                                if text:
                                    caption_msg = msg
                                    caption_text = text
                                    break

                            if caption_msg is None:
                                state.skipped += len(media_msgs)
                                return

                            if _is_telethon_forwarded(caption_msg):
                                if _contains_ad_text(caption_text, block_keywords):
                                    for msg in media_msgs:
                                        await _delete_message_with_reason(
                                            chat_id=chat_id,
                                            message_id=int(getattr(msg, "id", 0) or 0),
                                            media_key=f"mtproto:ad:{getattr(msg, 'id', 0)}",
                                            reason="ad_block",
                                            context=context,
                                        )
                                    state.skipped += len(media_msgs)
                                    return

                            sender_id = getattr(caption_msg, "sender_id", None)
                            if sender_id is not None and int(sender_id) == bot_id:
                                state.skipped += len(media_msgs)
                                return

                            tags = _extract_hashtags_telethon(caption_text, getattr(caption_msg, "entities", None))
                            mapped = _apply_tag_aliases(tags, alias_map) if tags else []
                            if mapped:
                                async with db_lock:
                                    db.record_tags(chat_id=chat_id, tags=mapped)
                                for tag in mapped:
                                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                                tag_counts_dirty = True

                            if tag_counts_dirty:
                                tag_counts_list = sorted(tag_counts.items(), key=lambda t: (-t[1], t[0]))
                                tag_counts_dirty = False

                            base_text = _strip_hashtags(caption_text)
                            cleaned = _apply_text_block(base_text, block_keywords)
                            needed = max(tag_count - len(mapped), 0)
                            if needed <= 0:
                                state.skipped += len(media_msgs)
                                return

                            matched = _match_tags_excluding(cleaned, tag_counts_list, max_tags=needed, exclude=mapped)
                            if not matched:
                                state.skipped += len(media_msgs)
                                return

                            new_tags = [*mapped, *matched]
                            new_caption = _build_tag_caption(cleaned, new_tags)
                            pairs = await _copy_and_replace_messages(
                                chat_id=chat_id,
                                messages=media_msgs,
                                caption_message_id=int(getattr(caption_msg, "id", 0) or 0),
                                caption_text=new_caption,
                                apply_caption_to_all=True,
                                context=context,
                            )
                            if not pairs:
                                state.failed += len(media_msgs)
                                return

                            async with db_lock:
                                for old_id, new_id in pairs:
                                    db.add_tag_build_sent(chat_id=chat_id, message_id=new_id)
                                    db.replace_message_id(
                                        chat_id=chat_id,
                                        old_message_id=old_id,
                                        new_message_id=new_id,
                                    )
                            state.tagged += len(pairs)

                        current_group_id: int | None = None
                        group_msgs: list[Any] = []
                        processed = 0
                        stop_after_group = False

                        async def flush_group() -> None:
                            nonlocal current_group_id, group_msgs
                            if not group_msgs:
                                return
                            await handle_group(group_msgs)
                            group_msgs = []
                            current_group_id = None

                        async for msg in client.iter_messages(entity, limit=None, reverse=False):
                            if msg is None or msg.id is None:
                                state.skipped += 1
                                await progress_cb()
                                continue
                            processed += 1
                            state.scanned += 1
                            if getattr(msg, "photo", None) is None and getattr(msg, "document", None) is None:
                                text = str(getattr(msg, "message", "") or "")
                                if "text" in blacklist and text and getattr(msg, "action", None) is None:
                                    await _delete_message_with_reason(
                                        chat_id=chat_id,
                                        message_id=int(getattr(msg, "id", 0) or 0),
                                        media_key=f"mtproto:text:{getattr(msg, 'id', 0)}",
                                        reason="media_blacklist:text",
                                        context=context,
                                    )
                                    await progress_cb()
                                    continue
                                if _is_telethon_forwarded(msg) and _contains_ad_text(text, block_keywords):
                                    await _delete_message_with_reason(
                                        chat_id=chat_id,
                                        message_id=int(getattr(msg, "id", 0) or 0),
                                        media_key=f"mtproto:ad:{getattr(msg, 'id', 0)}",
                                        reason="ad_block",
                                        context=context,
                                    )
                                    await progress_cb()
                                    continue

                            grouped_id = getattr(msg, "grouped_id", None)
                            if limit > 0 and processed >= limit:
                                stop_after_group = True

                            if grouped_id:
                                if current_group_id is None:
                                    current_group_id = grouped_id
                                if grouped_id != current_group_id:
                                    await flush_group()
                                    if stop_after_group:
                                        break
                                    current_group_id = grouped_id
                                group_msgs.append(msg)
                            else:
                                if current_group_id is not None:
                                    await flush_group()
                                    if stop_after_group:
                                        break
                                await handle_group([msg])
                                if stop_after_group:
                                    break
                            await progress_cb()

                        await flush_group()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "tag_build 完成："
                    f"scanned={state.scanned} tagged={state.tagged} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "tag_build 已取消："
                    f"scanned={state.scanned} tagged={state.tagged} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "tag_build 失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} tagged={state.tagged} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
            finally:
                tag_build_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            tag_build_tasks[chat_id] = context.application.create_task(runner())
        else:
            tag_build_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_tag_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        if context.args:
            await context.bot.send_message(chat_id=chat_id, text="该命令不支持参数，用法：/tag_build")
            return
        limit = max(0, int(config.tag_build_limit))
        await _start_tag_build(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=limit,
            context=context,
        )

    async def _cmd_tag_build_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        state = tag_build_states.get(chat_id)
        if state is None:
            await context.bot.send_message(chat_id=chat_id, text="当前无 tag_build 任务")
            return
        status = "running" if state.running else "stopped"
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"tag_build_status={status} scanned={state.scanned} tagged={state.tagged} "
                f"skipped={state.skipped} failed={state.failed} limit={state.limit}"
                + (f"\nlast_error={state.last_error}" if state.last_error else "")
            ),
        )

    async def _cmd_tag_build_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        task = tag_build_tasks.get(chat_id)
        if task is None or task.done():
            await context.bot.send_message(chat_id=chat_id, text="当前无 tag_build 任务")
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        task.cancel()
        await context.bot.send_message(chat_id=chat_id, text="已请求取消 tag_build")

    async def _cmd_tag_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not context.args:
            current = await _effective_tag_count(chat_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"当前 tag_count={current}（范围 1-10）",
            )
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        raw = context.args[0].strip()
        try:
            value = int(raw)
        except ValueError:
            await context.bot.send_message(chat_id=chat_id, text="用法：/tag_count 1-10")
            return
        if value < 1 or value > 10:
            await context.bot.send_message(chat_id=chat_id, text="用法：/tag_count 1-10")
            return
        async with db_lock:
            db.set_setting(_chat_setting_key(chat_id, "tag_count"), str(value))
        await context.bot.send_message(chat_id=chat_id, text=f"已设置 tag_count={value}")

    async def _cmd_tag_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return

        args = [arg.strip() for arg in context.args if arg.strip()]
        scope = "chat"
        if args and args[0].lower() in {"global", "all"}:
            scope = "global"
            args = args[1:]

        action = args[0].strip().lower() if args else "list"
        path = _tag_aliases_global_path() if scope == "global" else _tag_aliases_path(chat_id)
        global_path = _tag_aliases_global_path()
        chat_path = _tag_aliases_path(chat_id)

        def _render_list(label: str, mapping: dict[str, str], file_path: Path) -> str:
            if not mapping:
                return f"{label}为空（文件：{file_path}）"
            lines = [f"#{old}=#{mapping[old]}" for old in sorted(mapping.keys())]
            preview = "\n".join(lines[:50])
            suffix = "\n（仅展示前 50 条）" if len(lines) > 50 else ""
            return f"{label}（文件：{file_path}）：\n{preview}{suffix}"

        if action in {"list", "ls"}:
            mapping = await _sync_tag_aliases(chat_id)
            if scope == "global":
                global_mapping = _parse_tag_alias_file(global_path)
                text = _render_list("tag_rename(全局)", global_mapping, global_path)
            else:
                global_mapping = _parse_tag_alias_file(global_path)
                chat_mapping = _parse_tag_alias_file(chat_path)
                text = "\n\n".join(
                    [
                        _render_list("tag_rename(全局)", global_mapping, global_path),
                        _render_list(f"tag_rename(群组 {chat_id})", chat_mapping, chat_path),
                    ]
                )
            await context.bot.send_message(chat_id=chat_id, text=text)
            return

        if action in {"del", "remove", "rm"}:
            if len(args) < 2:
                await context.bot.send_message(chat_id=chat_id, text="用法：/tag_rename [global] del #旧标签")
                return
            old_tag = _normalize_tag_text(args[1])
            if not old_tag:
                await context.bot.send_message(chat_id=chat_id, text="用法：/tag_rename [global] del #旧标签")
                return
            mapping = _parse_tag_alias_file(path)
            if old_tag in mapping:
                mapping.pop(old_tag, None)
                _write_tag_alias_file(path, mapping)
                await _sync_tag_aliases(chat_id)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"已删除 tag_rename（{scope}）：#{old_tag}",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"未找到 tag_rename（{scope}）：#{old_tag}",
                )
            return

        expr = " ".join(args).strip()
        if "=" not in expr:
            await context.bot.send_message(chat_id=chat_id, text="用法：/tag_rename [global] #旧=#新")
            return
        left, right = expr.split("=", 1)
        old_tag = _normalize_tag_text(left)
        new_tag = _normalize_tag_text(right)
        if not old_tag or not new_tag:
            await context.bot.send_message(chat_id=chat_id, text="用法：/tag_rename [global] #旧=#新")
            return
        if old_tag == new_tag:
            await context.bot.send_message(chat_id=chat_id, text="旧标签与新标签相同，无需设置")
            return
        mapping = _parse_tag_alias_file(path)
        mapping[old_tag] = new_tag
        _write_tag_alias_file(path, mapping)
        await _sync_tag_aliases(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"已设置 tag_rename（{scope}）：#{old_tag}=#{new_tag}",
        )

    async def _start_tag_update(
        *,
        chat_id: int,
        chat_username: str | None,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in tag_rebuild_tasks and not tag_rebuild_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有 tag_rebuild 任务在运行，请稍后再试")
            return
        if chat_id in tag_tasks and not tag_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有标签目录任务在运行，请稍后再试")
            return

        async def after_success() -> None:
            await _start_tags_pin(
                chat_id=chat_id,
                chat_username=chat_username,
                limit=0,
                max_tags=0,
                context=context,
                target_chat=None,
            )

        await _start_tag_rebuild(
            chat_id=chat_id,
            chat_username=chat_username,
            limit=0,
            context=context,
            after_success=after_success,
        )

    async def _start_tag_rebuild(
        *,
        chat_id: int,
        chat_username: str | None,
        limit: int,
        context: ContextTypes.DEFAULT_TYPE,
        after_success: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if chat_id in tag_rebuild_tasks and not tag_rebuild_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有 tag_rebuild 任务在运行，请稍后再试")
            return

        state = TagRebuildState(started_at=int(time.time()), limit=limit, last_progress_at=int(time.time()))
        tag_rebuild_states[chat_id] = state

        start_text = f"开始 tag_rebuild：target={chat_username or chat_id} limit={limit or 'all'}"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"tag_rebuild 进行中：scanned={state.scanned} rebuilt={state.rebuilt} "
                        f"skipped={state.skipped} failed={state.failed} limit={limit or 'all'}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("tag_rebuild 需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行 tag_rebuild")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        alias_map = await _sync_tag_aliases(chat_id)
                        async with db_lock:
                            tag_counts = {tag: count for tag, count in db.list_tag_counts(chat_id=chat_id)}
                            db_keywords = db.list_text_block_keywords()
                            raw_tag_count = db.get_setting(_chat_setting_key(chat_id, "tag_count"))
                            raw_blacklist = db.get_setting(_media_blacklist_key(chat_id))

                        block_keywords = _collect_text_block_keywords(chat_id=chat_id, db_keywords=db_keywords)
                        tag_count = _parse_int_setting(raw_tag_count, min_value=1, max_value=10) or config.tag_count
                        blacklist = _parse_media_blacklist(raw_blacklist)
                        tag_counts_list = sorted(tag_counts.items(), key=lambda t: (-t[1], t[0]))
                        tag_counts_dirty = False
                        bot_id = int(context.bot.id)
                        async def handle_group(messages: list[Any]) -> None:
                            nonlocal tag_counts_dirty, tag_counts_list
                            media_msgs = [
                                msg
                                for msg in messages
                                if getattr(msg, "photo", None) is not None or getattr(msg, "document", None) is not None
                            ]
                            if not media_msgs:
                                state.skipped += len(messages)
                                return

                            if blacklist:
                                blocked_msgs: list[tuple[Any, str]] = []
                                for msg in media_msgs:
                                    media_type = _telethon_media_type(msg)
                                    if media_type and media_type in blacklist:
                                        blocked_msgs.append((msg, media_type))
                                if blocked_msgs:
                                    for msg, media_type in blocked_msgs:
                                        await _delete_message_with_reason(
                                            chat_id=chat_id,
                                            message_id=int(getattr(msg, "id", 0) or 0),
                                            media_key=f"mtproto:{media_type}:{getattr(msg, 'id', 0)}",
                                            reason=f"media_blacklist:{media_type}",
                                            context=context,
                                        )
                                    state.skipped += len(media_msgs)
                                    return

                            caption_msg = None
                            caption_text = ""
                            for msg in media_msgs:
                                text = str(getattr(msg, "message", "") or "")
                                if text:
                                    caption_msg = msg
                                    caption_text = text
                                    break

                            if caption_msg is None:
                                state.skipped += len(media_msgs)
                                return

                            if _is_telethon_forwarded(caption_msg):
                                if _contains_ad_text(caption_text, block_keywords):
                                    for msg in media_msgs:
                                        await _delete_message_with_reason(
                                            chat_id=chat_id,
                                            message_id=int(getattr(msg, "id", 0) or 0),
                                            media_key=f"mtproto:ad:{getattr(msg, 'id', 0)}",
                                            reason="ad_block",
                                            context=context,
                                        )
                                    state.skipped += len(media_msgs)
                                    return

                            sender_id = getattr(caption_msg, "sender_id", None)
                            if sender_id is not None and int(sender_id) == bot_id:
                                state.skipped += len(media_msgs)
                                return

                            tags = _extract_hashtags_telethon(caption_text, getattr(caption_msg, "entities", None))
                            mapped = _apply_tag_aliases(tags, alias_map)

                            base_text = _strip_hashtags(caption_text)
                            cleaned = _apply_text_block(base_text, block_keywords)
                            if mapped:
                                async with db_lock:
                                    db.record_tags(chat_id=chat_id, tags=mapped)
                                for tag in mapped:
                                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                                tag_counts_dirty = True

                            if tag_counts_dirty:
                                tag_counts_list = sorted(tag_counts.items(), key=lambda t: (-t[1], t[0]))
                                tag_counts_dirty = False

                            needed = max(tag_count - len(mapped), 0)
                            matched: list[str] = []
                            if needed > 0:
                                matched = _match_tags_excluding(
                                    cleaned,
                                    tag_counts_list,
                                    max_tags=needed,
                                    exclude=mapped,
                                )

                            has_tags = bool(mapped) or bool(matched)
                            should_rebuild = True

                            if has_tags:
                                new_caption = _build_tag_caption(cleaned, [*mapped, *matched])
                            else:
                                should_rebuild = cleaned != caption_text
                                new_caption = cleaned

                            if not should_rebuild:
                                state.skipped += len(media_msgs)
                                return

                            pairs = await _copy_and_replace_messages(
                                chat_id=chat_id,
                                messages=media_msgs,
                                caption_message_id=int(getattr(caption_msg, "id", 0) or 0),
                                caption_text=new_caption,
                                apply_caption_to_all=True,
                                context=context,
                            )
                            if not pairs:
                                state.failed += len(media_msgs)
                                return

                            async with db_lock:
                                for old_id, new_id in pairs:
                                    db.add_tag_build_sent(chat_id=chat_id, message_id=new_id)
                                    db.replace_message_id(
                                        chat_id=chat_id,
                                        old_message_id=old_id,
                                        new_message_id=new_id,
                                    )

                            state.rebuilt += len(pairs)

                        current_group_id: int | None = None
                        group_msgs: list[Any] = []
                        processed = 0
                        stop_after_group = False

                        async def flush_group() -> None:
                            nonlocal current_group_id, group_msgs
                            if not group_msgs:
                                return
                            await handle_group(group_msgs)
                            group_msgs = []
                            current_group_id = None

                        reverse_scan = limit == 0
                        async for msg in client.iter_messages(entity, limit=None, reverse=reverse_scan):
                            if msg is None or msg.id is None:
                                state.skipped += 1
                                await progress_cb()
                                continue
                            processed += 1
                            state.scanned += 1
                            if getattr(msg, "photo", None) is None and getattr(msg, "document", None) is None:
                                text = str(getattr(msg, "message", "") or "")
                                if "text" in blacklist and text and getattr(msg, "action", None) is None:
                                    await _delete_message_with_reason(
                                        chat_id=chat_id,
                                        message_id=int(getattr(msg, "id", 0) or 0),
                                        media_key=f"mtproto:text:{getattr(msg, 'id', 0)}",
                                        reason="media_blacklist:text",
                                        context=context,
                                    )
                                    await progress_cb()
                                    continue
                                if _is_telethon_forwarded(msg) and _contains_ad_text(text, block_keywords):
                                    await _delete_message_with_reason(
                                        chat_id=chat_id,
                                        message_id=int(getattr(msg, "id", 0) or 0),
                                        media_key=f"mtproto:ad:{getattr(msg, 'id', 0)}",
                                        reason="ad_block",
                                        context=context,
                                    )
                                    await progress_cb()
                                    continue

                            grouped_id = getattr(msg, "grouped_id", None)
                            if limit > 0 and processed >= limit:
                                stop_after_group = True

                            if grouped_id:
                                if current_group_id is None:
                                    current_group_id = grouped_id
                                if grouped_id != current_group_id:
                                    await flush_group()
                                    if stop_after_group:
                                        break
                                    current_group_id = grouped_id
                                group_msgs.append(msg)
                            else:
                                if current_group_id is not None:
                                    await flush_group()
                                    if stop_after_group:
                                        break
                                await handle_group([msg])
                                if stop_after_group:
                                    break
                            await progress_cb()

                        await flush_group()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "tag_rebuild 完成："
                    f"scanned={state.scanned} rebuilt={state.rebuilt} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
                if after_success is not None:
                    try:
                        await after_success()
                    except Exception as exc:  # noqa: BLE001
                        await _send_message_with_retry(
                            context,
                            chat_id=chat_id,
                            text=f"标签更新后续步骤失败：{_telethon_error_text(exc)}",
                        )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "tag_rebuild 已取消："
                    f"scanned={state.scanned} rebuilt={state.rebuilt} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "tag_rebuild 失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} rebuilt={state.rebuilt} "
                    f"skipped={state.skipped} failed={state.failed}"
                )
            finally:
                tag_rebuild_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            tag_rebuild_tasks[chat_id] = context.application.create_task(runner())
        else:
            tag_rebuild_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_tag_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        limit = max(0, int(config.tag_build_limit))
        if context.args:
            token = context.args[0].strip().lower()
            if token == "all":
                limit = 0
            elif token.isdigit():
                limit = int(token)
            else:
                await context.bot.send_message(chat_id=chat_id, text="用法：/tag_rebuild [N|all]")
                return

        await _start_tag_rebuild(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=limit,
            context=context,
        )

    async def _cmd_tag_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        await _start_tag_update(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            context=context,
        )

    async def _start_text_purge(
        *,
        chat_id: int,
        chat_username: str | None,
        limit: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in text_purge_tasks and not text_purge_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有清理任务在运行，请稍后再试")
            return

        state = TextPurgeState(started_at=int(time.time()), limit=limit, last_progress_at=int(time.time()))
        text_purge_states[chat_id] = state

        start_text = f"开始清理纯文字：target={chat_username or chat_id} limit={limit or 'all'}"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"清理中：scanned={state.scanned} deleted={state.deleted} "
                        f"failed={state.failed} limit={limit or 'all'}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("清理纯文字需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行清理")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        processed = 0
                        async for msg in client.iter_messages(entity, limit=None, reverse=True):
                            if msg is None or msg.id is None:
                                state.scanned += 1
                                await progress_cb()
                                continue
                            processed += 1
                            state.scanned += 1
                            if limit > 0 and processed > limit:
                                break

                            if getattr(msg, "media", None) is not None:
                                await progress_cb()
                                continue
                            if getattr(msg, "message", None) is None:
                                await progress_cb()
                                continue
                            if getattr(msg, "action", None) is not None:
                                await progress_cb()
                                continue
                            try:
                                await client.delete_messages(entity, [int(msg.id)])
                                state.deleted += 1
                            except Exception:  # noqa: BLE001
                                state.failed += 1
                            await progress_cb()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "清理完成："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "清理已取消："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "清理失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            finally:
                text_purge_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            text_purge_tasks[chat_id] = context.application.create_task(runner())
        else:
            text_purge_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_text_purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        await _start_text_purge(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=0,
            context=context,
        )

    async def _start_media_filter(
        *,
        chat_id: int,
        chat_username: str | None,
        settings: MediaFilterSettings,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in media_filter_tasks and not media_filter_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有媒体筛选任务在运行，请稍后再试")
            return

        state = MediaFilterState(started_at=int(time.time()), last_progress_at=int(time.time()))
        media_filter_states[chat_id] = state

        start_text = "开始媒体筛选：target=" + str(chat_username or chat_id) + "\n" + _format_media_filter_summary(settings)
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        "筛选中："
                        f"scanned={state.scanned} deleted={state.deleted} "
                        f"kept={state.kept} failed={state.failed}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("媒体筛选需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行筛选")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        async for msg in client.iter_messages(entity, limit=None, reverse=True):
                            if msg is None or msg.id is None:
                                state.scanned += 1
                                state.kept += 1
                                await progress_cb()
                                continue
                            state.scanned += 1
                            if getattr(msg, "action", None) is not None:
                                state.kept += 1
                                await progress_cb()
                                continue

                            has_media = getattr(msg, "photo", None) is not None or getattr(msg, "document", None) is not None
                            if not has_media:
                                if not settings.include_text:
                                    state.kept += 1
                                    await progress_cb()
                                    continue
                                text = str(getattr(msg, "message", "") or "")
                                if not text:
                                    state.kept += 1
                                    await progress_cb()
                                    continue
                                media_type = "text"
                                size_bytes = None
                                duration_sec = None
                                is_text = True
                            else:
                                media_type = _telethon_media_type(msg) or "document"
                                size_bytes = _telethon_media_size(msg)
                                duration_sec = _telethon_media_duration(msg)
                                is_text = False

                            if _media_filter_matches(
                                settings,
                                media_type=media_type,
                                is_text=is_text,
                                size_bytes=size_bytes,
                                duration_sec=duration_sec,
                            ):
                                state.kept += 1
                                await progress_cb()
                                continue

                            try:
                                await client.delete_messages(entity, [int(msg.id)])
                                state.deleted += 1
                            except Exception:  # noqa: BLE001
                                state.failed += 1
                            await progress_cb()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "媒体筛选完成："
                    f"scanned={state.scanned} deleted={state.deleted} "
                    f"kept={state.kept} failed={state.failed}"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "媒体筛选已取消："
                    f"scanned={state.scanned} deleted={state.deleted} "
                    f"kept={state.kept} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "媒体筛选失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} deleted={state.deleted} "
                    f"kept={state.kept} failed={state.failed}"
                )
            finally:
                media_filter_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            media_filter_tasks[chat_id] = context.application.create_task(runner())
        else:
            media_filter_tasks[chat_id] = asyncio.create_task(runner())

    async def _start_batch_delete(
        *,
        chat_id: int,
        chat_username: str | None,
        limit: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in batch_delete_tasks and not batch_delete_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有批量删除任务在运行，请稍后再试")
            return
        if limit <= 0:
            await context.bot.send_message(chat_id=chat_id, text="请输入有效条数（1-1000）")
            return
        limit = min(limit, 1000)

        state = BatchDeleteState(started_at=int(time.time()), limit=limit, last_progress_at=int(time.time()))
        batch_delete_states[chat_id] = state

        start_text = f"开始批量删除：target={chat_username or chat_id} limit={limit}"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"批量删除中：scanned={state.scanned} deleted={state.deleted} "
                        f"failed={state.failed} limit={limit}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("批量删除需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行删除")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        async for msg in client.iter_messages(entity, limit=limit, reverse=False):
                            state.scanned += 1
                            if msg is None or msg.id is None:
                                await progress_cb()
                                continue
                            try:
                                await client.delete_messages(entity, [int(msg.id)])
                                state.deleted += 1
                            except Exception:  # noqa: BLE001
                                state.failed += 1
                            await progress_cb()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "批量删除完成："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "批量删除已取消："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "批量删除失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            finally:
                batch_delete_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            batch_delete_tasks[chat_id] = context.application.create_task(runner())
        else:
            batch_delete_tasks[chat_id] = asyncio.create_task(runner())

    async def _start_delete_by_text(
        *,
        chat_id: int,
        chat_username: str | None,
        keyword: str,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in delete_by_text_tasks and not delete_by_text_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有删除任务在运行，请稍后再试")
            return
        keyword = keyword.strip()
        if not keyword:
            await context.bot.send_message(chat_id=chat_id, text="请输入非空文本")
            return

        display_keyword = keyword if len(keyword) <= 200 else keyword[:200] + "…"
        state = DeleteByTextState(
            started_at=int(time.time()),
            keyword=keyword,
            last_progress_at=int(time.time()),
        )
        delete_by_text_states[chat_id] = state

        start_text = f"开始删除特定媒体：target={chat_username or chat_id} 关键词={display_keyword}"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb() -> None:
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"删除中：scanned={state.scanned} deleted={state.deleted} "
                        f"failed={state.failed} 关键词={display_keyword}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                if config.tg_api_id is None or not config.tg_api_hash:
                    raise RuntimeError("删除特定媒体需要 TG_API_ID/TG_API_HASH")
                try:
                    from telethon import TelegramClient  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError("缺少 Telethon 依赖：请先 pip install -r requirements-scan.txt") from exc

                keyword_folded = keyword.casefold()

                async with telethon_lock:
                    client = TelegramClient(config.tg_session, config.tg_api_id, config.tg_api_hash)
                    await client.connect()
                    try:
                        authorized = await client.is_user_authorized()
                        if not authorized:
                            raise RuntimeError("未检测到 Telethon 用户账号 session：请先在私聊执行 /session_login 完成授权")
                        me = await client.get_me()
                        if getattr(me, "bot", False):
                            raise RuntimeError("当前 TG_SESSION 为 bot 账号，无法执行删除")

                        entity = await _resolve_entity_telethon(
                            client,
                            chat=None,
                            bot_chat_id=chat_id,
                            bot_chat_username=chat_username,
                            allow_dialog_lookup=True,
                        )

                        async for msg in client.iter_messages(entity, limit=None, reverse=True):
                            if msg is None or msg.id is None:
                                state.scanned += 1
                                await progress_cb()
                                continue
                            state.scanned += 1
                            if getattr(msg, "action", None) is not None:
                                await progress_cb()
                                continue
                            text = str(getattr(msg, "message", "") or "")
                            if not text:
                                await progress_cb()
                                continue
                            if getattr(msg, "media", None) is None:
                                await progress_cb()
                                continue
                            if keyword_folded not in text.casefold():
                                await progress_cb()
                                continue
                            try:
                                await client.delete_messages(entity, [int(msg.id)])
                                state.deleted += 1
                            except Exception:  # noqa: BLE001
                                state.failed += 1
                            await progress_cb()
                    finally:
                        await client.disconnect()

                state.running = False
                await finalize(
                    "删除完成："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    "删除已取消："
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    "删除失败："
                    f"{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} deleted={state.deleted} failed={state.failed}"
                )
            finally:
                delete_by_text_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            delete_by_text_tasks[chat_id] = context.application.create_task(runner())
        else:
            delete_by_text_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_tag_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return

        stopped: list[str] = []

        task = scan_tasks.get(chat_id)
        if task is not None and not task.done():
            task.cancel()
            stopped.append("scan")

        task = tag_tasks.get(chat_id)
        if task is not None and not task.done():
            task.cancel()
            stopped.append("tags_pin")

        task = tag_build_tasks.get(chat_id)
        if task is not None and not task.done():
            task.cancel()
            stopped.append("tag_build")

        task = tag_rebuild_tasks.get(chat_id)
        if task is not None and not task.done():
            task.cancel()
            stopped.append("tag_rebuild")

        if not stopped:
            await context.bot.send_message(chat_id=chat_id, text="当前无进行中的任务")
            return
        await context.bot.send_message(chat_id=chat_id, text="已请求取消：" + ", ".join(stopped))

    async def _start_scan(
        *,
        chat_id: int,
        chat_username: str | None,
        target_chat: str | None,
        limit: int,
        delete: bool,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if chat_id in scan_tasks and not scan_tasks[chat_id].done():
            await context.bot.send_message(chat_id=chat_id, text="已有扫描任务在运行，可用 /scan_status 查看或 /scan_stop 终止")
            return

        state = ScanState(started_at=int(time.time()), limit=limit, delete=delete, last_progress_at=int(time.time()))
        scan_states[chat_id] = state

        target_info = target_chat or (f"@{chat_username}" if chat_username else str(chat_id))
        start_text = f"开始扫描：target={target_info} limit={limit} delete={int(delete)} reverse=1"
        progress_msg = await context.bot.send_message(chat_id=chat_id, text=start_text)
        state.progress_message_id = int(progress_msg.message_id)

        async def progress_cb(result: ScanResult) -> None:
            state.scanned = result.scanned
            state.decided_delete = result.decided_delete
            state.deleted = result.deleted
            state.failed = result.failed
            now = int(time.time())
            if state.progress_message_id is None:
                return
            if now - state.last_progress_at < 3:
                return
            state.last_progress_at = now
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.progress_message_id,
                    text=(
                        f"扫描中：scanned={state.scanned} dup={state.decided_delete} "
                        f"deleted={state.deleted} failed={state.failed} limit={limit} delete={int(delete)}"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        async def finalize(summary: str) -> None:
            if state.progress_message_id is not None:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=state.progress_message_id)
                except Exception:  # noqa: BLE001
                    pass
                state.progress_message_id = None
            await _send_message_with_retry(context, chat_id=chat_id, text=summary)

        async def runner() -> None:
            try:
                async with telethon_lock:
                    await run_scan(
                        chat=target_chat,
                        bot_chat_id=chat_id,
                        bot_chat_username=chat_username,
                        limit=limit,
                        delete=delete,
                        reverse=True,
                        as_bot=False,
                        interactive=False,
                        progress_cb=progress_cb,
                    )
                state.running = False
                await finalize(
                    "扫描完成："
                    f"scanned={state.scanned} dup={state.decided_delete} "
                    f"deleted={state.deleted} failed={state.failed}\n"
                    "可用 /stats 查看统计，或 /flush 清理待删队列"
                )
            except asyncio.CancelledError:
                state.running = False
                await finalize(
                    f"扫描已取消：scanned={state.scanned} dup={state.decided_delete} "
                    f"deleted={state.deleted} failed={state.failed}"
                )
                return
            except Exception as exc:  # noqa: BLE001
                state.running = False
                state.last_error = str(exc)
                await finalize(
                    f"扫描失败：{_telethon_error_text(exc)}\n"
                    f"scanned={state.scanned} dup={state.decided_delete} "
                    f"deleted={state.deleted} failed={state.failed}"
                )
            finally:
                scan_tasks.pop(chat_id, None)

        if hasattr(context.application, "create_task"):
            scan_tasks[chat_id] = context.application.create_task(runner())
        else:
            scan_tasks[chat_id] = asyncio.create_task(runner())

    async def _cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        target_chat: str | None = None
        limit = 0
        if context.args:
            first = context.args[0].strip()
            if first.isdigit():
                limit = int(first)
            else:
                target_chat = first
                if len(context.args) >= 2:
                    second = context.args[1].strip()
                    if second.isdigit():
                        limit = int(second)
        limit = max(0, int(limit))
        await _start_scan(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=limit,
            delete=False,
            context=context,
            target_chat=target_chat,
        )

    async def _cmd_scan_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        dry_run, delete_enabled = await _effective_mode(chat_id)
        if dry_run or not delete_enabled:
            await context.bot.send_message(
                chat_id=chat_id,
                text="当前不允许删除：请先 /dry_run_off 并 /enable_delete，然后重试 /scan_delete",
            )
            return
        target_chat: str | None = None
        limit = 0
        if context.args:
            first = context.args[0].strip()
            if first.isdigit():
                limit = int(first)
            else:
                target_chat = first
                if len(context.args) >= 2:
                    second = context.args[1].strip()
                    if second.isdigit():
                        limit = int(second)
        limit = max(0, int(limit))
        await _start_scan(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=limit,
            delete=True,
            context=context,
            target_chat=target_chat,
        )

    async def _cmd_scan_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        state = scan_states.get(chat_id)
        if state is None:
            await context.bot.send_message(chat_id=chat_id, text="当前无扫描任务")
            return
        status = "running" if state.running else "stopped"
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"scan_status={status} scanned={state.scanned} dup={state.decided_delete} "
                f"deleted={state.deleted} failed={state.failed} limit={state.limit} delete={int(state.delete)}"
                + (f"\nlast_error={state.last_error}" if state.last_error else "")
            ),
        )

    async def _cmd_scan_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        task = scan_tasks.get(chat_id)
        if task is None or task.done():
            await context.bot.send_message(chat_id=chat_id, text="当前无扫描任务")
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return
        task.cancel()
        await context.bot.send_message(chat_id=chat_id, text="已请求取消扫描")

    async def _cmd_tags_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return

        target_chat: str | None = None
        limit = 0
        max_tags = 0

        if context.args:
            first = context.args[0].strip()
            if first.isdigit():
                limit = int(first)
                if len(context.args) >= 2 and context.args[1].strip().isdigit():
                    max_tags = int(context.args[1].strip())
            else:
                target_chat = first
                if len(context.args) >= 2 and context.args[1].strip().isdigit():
                    limit = int(context.args[1].strip())
                if len(context.args) >= 3 and context.args[2].strip().isdigit():
                    max_tags = int(context.args[2].strip())

        limit = max(0, int(limit))
        max_tags = max(0, min(int(max_tags), 5000))

        await _start_tags_pin(
            chat_id=chat_id,
            chat_username=getattr(update.effective_chat, "username", None),
            limit=limit,
            max_tags=max_tags,
            context=context,
            target_chat=target_chat,
        )

    async def _cmd_flush(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        chat_id = int(update.effective_chat.id)
        if not _chat_allowed(config, chat_id):
            return
        if not await _require_admin(update, context):
            await context.bot.send_message(chat_id=chat_id, text="需要管理员权限")
            return

        limit = 100
        if context.args:
            try:
                limit = int(context.args[0])
            except ValueError:
                limit = 100
        limit = max(1, min(limit, 1000))

        async with db_lock:
            pending = db.list_pending_deletions(chat_id=chat_id, limit=limit)

        if not pending:
            await context.bot.send_message(chat_id=chat_id, text="无待删记录")
            return

        dry_run, delete_enabled = await _effective_mode(chat_id)
        if dry_run or not delete_enabled:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"删除未开启（DRY_RUN={int(dry_run)} DELETE_DUPLICATES={int(delete_enabled)}），待删={len(pending)}",
            )
            return

        deleted = 0
        failed = 0
        skipped = 0

        for message_id, media_key, reason in pending:
            async with db_lock:
                existing = db.get_deletion_record(chat_id, message_id)
                if existing is not None and (existing.result == "success" or not config.retry_failed_deletes):
                    if existing.result == "success":
                        db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
                    skipped += 1
                    continue

            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                async with db_lock:
                    db.record_deletion_attempt(
                        chat_id=chat_id,
                        message_id=message_id,
                        media_key=media_key,
                        result="failed",
                        error=str(exc),
                    )
                failed += 1
                continue

            async with db_lock:
                db.record_deletion_attempt(
                    chat_id=chat_id,
                    message_id=message_id,
                    media_key=media_key,
                    result="success",
                    error=None,
                )
                db.remove_pending_deletion(chat_id=chat_id, message_id=message_id)
            deleted += 1

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"flush 完成：deleted={deleted} failed={failed} skipped={skipped} total={len(pending)}",
        )

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.chat is None:
            return
        chat_id = int(message.chat.id)
        if not _chat_allowed(config, chat_id):
            return
        pending_tag = tag_add_inputs.get(chat_id)
        if pending_tag is not None:
            user_id = getattr(message.from_user, "id", None)
            if user_id is not None and int(user_id) == int(pending_tag.user_id):
                raw = (message.text or "").strip()
                if not raw:
                    await context.bot.send_message(chat_id=chat_id, text="请输入非空标签")
                    return
                entities = getattr(message, "entities", None)
                extracted = _extract_hashtags_bot(raw, entities)
                if extracted:
                    if len(extracted) != 1:
                        await context.bot.send_message(chat_id=chat_id, text="一次只能添加一个标签")
                        return
                    tag = _normalize_tag_text(extracted[0])
                else:
                    if any(char.isspace() for char in raw):
                        await context.bot.send_message(chat_id=chat_id, text="一次只能添加一个标签")
                        return
                    tag = _normalize_tag_text(raw)
                if not tag or not _is_valid_tag(tag):
                    await context.bot.send_message(chat_id=chat_id, text="标签格式不合法")
                    return
                async with db_lock:
                    db.record_tags(chat_id=chat_id, tags=[tag])
                tag_add_inputs.pop(chat_id, None)
                await context.bot.send_message(chat_id=chat_id, text=f"已添加标签：#{tag}")
                return
        pending_batch = batch_delete_inputs.get(chat_id)
        if pending_batch is not None:
            user_id = getattr(message.from_user, "id", None)
            if user_id is not None and int(user_id) == int(pending_batch.user_id):
                if pending_batch.step == "await_input":
                    raw = (message.text or "").strip()
                    if not raw.isdigit():
                        await context.bot.send_message(chat_id=chat_id, text="请输入有效数字")
                        return
                    count = int(raw)
                    if count < 1 or count > 1000:
                        await context.bot.send_message(chat_id=chat_id, text="范围 1-1000")
                        return
                    pending_batch.count = count
                    pending_batch.step = "await_confirm"
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"确认删除最新 {count} 条消息？",
                        reply_markup=_batch_delete_confirm_markup(),
                    )
                    return

        pending_input = delete_by_text_inputs.get(chat_id)
        if pending_input is not None:
            user_id = getattr(message.from_user, "id", None)
            if user_id is not None and int(user_id) == int(pending_input.user_id):
                if pending_input.step == "await_input":
                    keyword = (message.text or "").strip()
                    if not keyword:
                        await context.bot.send_message(chat_id=chat_id, text="请输入非空文本")
                        return
                    pending_input.keyword = keyword
                    pending_input.step = "await_confirm"
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"确认删除包含以下文本的消息（不区分大小写）：\n{keyword}",
                        reply_markup=_delete_by_text_confirm_markup(),
                    )
                    return

        pending_filter = media_filter_inputs.get(chat_id)
        if pending_filter is not None:
            user_id = getattr(message.from_user, "id", None)
            if user_id is not None and int(user_id) == int(pending_filter.user_id):
                if pending_filter.step == "await_size":
                    raw = (message.text or "").strip()
                    value = _parse_positive_float(raw)
                    if value is None:
                        await context.bot.send_message(chat_id=chat_id, text="请输入有效大小（单位 MB）")
                        return
                    settings = _get_media_filter_settings(chat_id)
                    settings.size_op = pending_filter.pending_op or "gt"
                    settings.size_mb = value
                    media_filter_inputs.pop(chat_id, None)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"已设置媒体大小：{_format_media_filter_size(settings)}",
                    )
                    await _send_media_filter_menu(chat_id=chat_id, context=context)
                    return
                if pending_filter.step == "await_duration":
                    raw = (message.text or "").strip()
                    value = _parse_positive_int(raw)
                    if value is None:
                        await context.bot.send_message(chat_id=chat_id, text="请输入有效时长（单位分钟）")
                        return
                    settings = _get_media_filter_settings(chat_id)
                    settings.duration_op = pending_filter.pending_op or "gt"
                    settings.duration_sec = value * 60
                    media_filter_inputs.pop(chat_id, None)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"已设置媒体时长：{_format_media_filter_duration(settings)}",
                    )
                    await _send_media_filter_menu(chat_id=chat_id, context=context)
                    return

        blacklist = await _effective_media_blacklist(chat_id)
        if "text" in blacklist:
            await _delete_message_with_reason(
                chat_id=chat_id,
                message_id=int(message.message_id),
                media_key=f"text:{message.message_id}",
                reason="media_blacklist:text",
                context=context,
            )
            return

        if _is_forwarded_message(message):
            text = message.text or ""
            async with db_lock:
                db_keywords = db.list_text_block_keywords()
            block_keywords = _collect_text_block_keywords(chat_id=chat_id, db_keywords=db_keywords)
            if _contains_ad_text(text, block_keywords):
                await _delete_message_with_reason(
                    chat_id=chat_id,
                    message_id=int(message.message_id),
                    media_key=f"ad:{message.message_id}",
                    reason="ad_block",
                    context=context,
                )
                return

    async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return

        item = _extract_media_item(message)
        if item is None:
            return

        if not _chat_allowed(config, item.chat_id):
            return

        blacklist = await _effective_media_blacklist(item.chat_id)
        blocked_type = _normalize_media_type(item.media_type)
        if blocked_type and blocked_type in blacklist:
            await _delete_message_with_reason(
                chat_id=item.chat_id,
                message_id=item.message_id,
                media_key=item.media_key,
                reason=f"media_blacklist:{blocked_type}",
                context=context,
            )
            return

        if _is_forwarded_message(message):
            text = message.caption or message.text or ""
            async with db_lock:
                db_keywords = db.list_text_block_keywords()
            block_keywords = _collect_text_block_keywords(chat_id=item.chat_id, db_keywords=db_keywords)
            if _contains_ad_text(text, block_keywords):
                await _delete_message_with_reason(
                    chat_id=item.chat_id,
                    message_id=item.message_id,
                    media_key=item.media_key,
                    reason="ad_block",
                    context=context,
                )
                return

        async with db_lock:
            decision = db.process_media(item)

        if decision.already_processed:
            return

        await _maybe_tag_build_message(
            message=message,
            item=item,
            decision=decision,
            context=context,
        )

        dry_run, delete_enabled = await _effective_mode(item.chat_id)

        if decision.message_id_to_delete is None:
            log.info(
                "keep chat=%s msg=%s key=%s reason=%s",
                item.chat_id,
                item.message_id,
                item.media_key,
                decision.reason,
            )
            return

        delete_id = decision.message_id_to_delete
        async with db_lock:
            db.add_pending_deletion(
                chat_id=item.chat_id,
                message_id=delete_id,
                media_key=item.media_key,
                reason=decision.reason,
            )

        if dry_run or not delete_enabled:
            log.warning(
                "dry_run skip_delete chat=%s delete_msg=%s canonical=%s key=%s reason=%s",
                item.chat_id,
                delete_id,
                decision.canonical_message_id,
                item.media_key,
                decision.reason,
            )
            return

        async with db_lock:
            existing = db.get_deletion_record(item.chat_id, delete_id)
            if existing is not None and (existing.result == "success" or not config.retry_failed_deletes):
                if existing.result == "success":
                    db.remove_pending_deletion(chat_id=item.chat_id, message_id=delete_id)
                log.info(
                    "skip_delete_already_attempted chat=%s delete_msg=%s result=%s",
                    item.chat_id,
                    delete_id,
                    existing.result,
                )
                return

        try:
            await context.bot.delete_message(chat_id=item.chat_id, message_id=delete_id)
        except Exception as exc:  # noqa: BLE001
            async with db_lock:
                db.record_deletion_attempt(
                    chat_id=item.chat_id,
                    message_id=delete_id,
                    media_key=item.media_key,
                    result="failed",
                    error=str(exc),
                )
            log.exception("delete_failed chat=%s msg=%s", item.chat_id, delete_id)
            return

        async with db_lock:
            db.record_deletion_attempt(
                chat_id=item.chat_id,
                message_id=delete_id,
                media_key=item.media_key,
                result="success",
                error=None,
            )
            db.remove_pending_deletion(chat_id=item.chat_id, message_id=delete_id)
        log.warning(
            "deleted chat=%s delete_msg=%s canonical=%s key=%s",
            item.chat_id,
            delete_id,
            decision.canonical_message_id,
            item.media_key,
        )

    async def _on_startup(application: Application) -> None:
        try:
            keywords = _parse_text_block_file(_text_block_global_path())
            log.info("startup_text_block_loaded count=%s", len(keywords))
        except Exception:  # noqa: BLE001
            log.exception("startup_text_block_load_failed")

        try:
            db.get_setting("startup_check")
            log.info("startup_db_check ok")
        except Exception:  # noqa: BLE001
            log.exception("startup_db_check_failed")

        commands_common = [
            BotCommand("start", "开始/说明"),
            BotCommand("help", "帮助"),
            BotCommand("menu", "按钮面板"),
            BotCommand("ping", "健康检查"),
            BotCommand("stats", "查看统计"),
            BotCommand("mode", "查看模式"),
            BotCommand("status", "查看任务状态"),
            BotCommand("tags_pin", "生成标签目录并置顶"),
            BotCommand("tag_pin", "生成标签目录并置顶(别名)"),
            BotCommand("tag_build", "补标签并重发"),
            BotCommand("tag_build_status", "查看tag_build进度"),
            BotCommand("tag_build_stop", "停止tag_build"),
            BotCommand("tag_rebuild", "历史标签重写"),
            BotCommand("tag_update", "标签更新"),
            BotCommand("tag_stop", "停止所有任务"),
            BotCommand("tag_count", "设置补标签数量"),
            BotCommand("tag_rename", "设置标签别名"),
            BotCommand("text_block", "管理屏蔽关键词"),
            BotCommand("enable_delete", "开启删除"),
            BotCommand("disable_delete", "关闭删除"),
            BotCommand("dry_run_on", "开启dry-run"),
            BotCommand("dry_run_off", "关闭dry-run"),
            BotCommand("scan", "回溯扫描(dry-run)"),
            BotCommand("scan_delete", "回溯扫描并删除"),
            BotCommand("scan_status", "扫描状态"),
            BotCommand("scan_stop", "停止扫描"),
            BotCommand("flush", "清理待删队列"),
        ]
        commands_private = [
            *commands_common,
            BotCommand("session_status", "Telethon会话状态"),
            BotCommand("session_qr", "二维码登录(推荐)"),
            BotCommand("session_login", "登录用户账号(发验证码)"),
            BotCommand("session_code", "提交验证码完成登录"),
            BotCommand("session_password", "提交两步验证密码"),
            BotCommand("session_logout", "注销用户账号"),
            BotCommand("session_reset", "重置session(删除本地文件)"),
        ]
        try:
            await application.bot.set_my_commands(commands_common, scope=BotCommandScopeDefault())
            await application.bot.set_my_commands(commands_private, scope=BotCommandScopeAllPrivateChats())
            await application.bot.set_my_commands(commands_common, scope=BotCommandScopeAllGroupChats())
            log.info("set_my_commands ok")
        except Exception:  # noqa: BLE001
            log.exception("set_my_commands_failed")

        if admin_id is not None:
            try:
                await application.bot.send_message(chat_id=admin_id, text="🤖 频道管家已上线，配置加载完毕。")
            except Exception as exc:  # noqa: BLE001
                log.warning("startup_notify_failed admin_id=%s error=%s", admin_id, exc)

    builder = Application.builder().token(config.bot_token)
    if hasattr(builder, "post_init"):
        builder = builder.post_init(_on_startup)
    else:
        log.warning("ApplicationBuilder 缺少 post_init，无法执行启动初始化")
    application = builder.build()
    application.add_handler(
        ChatMemberHandler(_on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER),
        group=-2,
    )
    application.add_handler(MessageHandler(filters.ALL, _track_message_chat), group=-1)
    application.add_handler(CommandHandler("ping", _cmd_ping))
    application.add_handler(CommandHandler("start", _cmd_start))
    application.add_handler(CommandHandler("help", _cmd_help))
    application.add_handler(CommandHandler("menu", _cmd_menu))
    application.add_handler(CommandHandler("stats", _cmd_stats))
    application.add_handler(CommandHandler("mode", _cmd_mode))
    application.add_handler(CommandHandler("status", _cmd_status))
    application.add_handler(CommandHandler("enable_delete", _cmd_enable_delete))
    application.add_handler(CommandHandler("disable_delete", _cmd_disable_delete))
    application.add_handler(CommandHandler("dry_run_on", _cmd_dry_run_on))
    application.add_handler(CommandHandler("dry_run_off", _cmd_dry_run_off))
    application.add_handler(CommandHandler("scan", _cmd_scan))
    application.add_handler(CommandHandler("scan_delete", _cmd_scan_delete))
    application.add_handler(CommandHandler("scan_status", _cmd_scan_status))
    application.add_handler(CommandHandler("scan_stop", _cmd_scan_stop))
    application.add_handler(CommandHandler("tags_pin", _cmd_tags_pin))
    application.add_handler(CommandHandler("tag_pin", _cmd_tags_pin))
    application.add_handler(CommandHandler("tag_build", _cmd_tag_build))
    application.add_handler(CommandHandler("tag_build_status", _cmd_tag_build_status))
    application.add_handler(CommandHandler("tag_build_stop", _cmd_tag_build_stop))
    application.add_handler(CommandHandler("tag_rebuild", _cmd_tag_rebuild))
    application.add_handler(CommandHandler("tag_update", _cmd_tag_update))
    application.add_handler(CommandHandler("tag_count", _cmd_tag_count))
    application.add_handler(CommandHandler("tag_rename", _cmd_tag_rename))
    application.add_handler(CommandHandler("tag_stop", _cmd_tag_stop))
    application.add_handler(CommandHandler("text_block", _cmd_text_block))
    application.add_handler(CommandHandler("flush", _cmd_flush))
    application.add_handler(CommandHandler("session_status", _cmd_session_status))
    application.add_handler(CommandHandler("session_login", _cmd_session_login))
    application.add_handler(CommandHandler("session_qr", _cmd_session_qr))
    application.add_handler(CommandHandler("session_code", _cmd_session_code))
    application.add_handler(CommandHandler("session_password", _cmd_session_password))
    application.add_handler(CommandHandler("session_logout", _cmd_session_logout))
    application.add_handler(CommandHandler("session_reset", _cmd_session_reset))
    application.add_handler(CallbackQueryHandler(_handle_callback, pattern="^cm:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(MessageHandler(filters.ATTACHMENT, on_media))

    log.info(
        "starting bot default_dry_run=%s default_delete_duplicates=%s allow_chat_ids=%s db=%s",
        config.dry_run,
        config.delete_duplicates,
        sorted(config.allow_chat_ids) if config.allow_chat_ids else None,
        config.db_path,
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=())
