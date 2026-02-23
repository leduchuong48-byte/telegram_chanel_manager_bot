from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_dotenv_from_file(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not _ENV_KEY_RE.fullmatch(key):
            continue

        if not value:
            os.environ.setdefault(key, "")
            continue

        os.environ.setdefault(key, _strip_quotes(value))


def _load_dotenv() -> None:
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / ".env"
        if candidate.is_file():
            _load_dotenv_from_file(candidate)
            return


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _parse_chat_ids(value: str | None) -> frozenset[int] | None:
    if value is None:
        return None
    raw = [part.strip() for part in value.split(",")]
    raw = [part for part in raw if part]
    if not raw:
        return None
    return frozenset(int(part) for part in raw)


@dataclass
class Config:
    bot_token: str | None
    db_path: Path
    allow_chat_ids: frozenset[int] | None
    delete_duplicates: bool
    dry_run: bool
    keep_policy: str
    retry_failed_deletes: bool
    log_level: str
    tag_build_limit: int
    tag_count: int

    # Telethon（可选）
    tg_api_id: int | None
    tg_api_hash: str | None
    tg_session: str


def load_config() -> Config:
    _load_dotenv()
    bot_token = os.getenv("TG_BOT_TOKEN", "").strip() or None

    db_path = Path(os.getenv("DB_PATH", "./data/bot.db")).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    keep_policy = os.getenv("KEEP_POLICY", "oldest").strip().lower()
    if keep_policy not in {"oldest"}:
        raise RuntimeError(f"不支持的 KEEP_POLICY: {keep_policy}")

    tg_api_id_raw = os.getenv("TG_API_ID", "").strip()
    tg_api_id = int(tg_api_id_raw) if tg_api_id_raw else None
    tg_session = os.getenv("TG_SESSION", "").strip() or "./sessions/user"
    try:
        session_path = Path(tg_session).expanduser()
        session_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    tag_build_limit_raw = os.getenv("TAG_BUILD_LIMIT", "").strip()
    if tag_build_limit_raw:
        try:
            tag_build_limit = int(tag_build_limit_raw)
        except ValueError as exc:
            raise RuntimeError("TAG_BUILD_LIMIT 必须是整数") from exc
        if tag_build_limit < 0:
            tag_build_limit = 0
    else:
        tag_build_limit = 0

    tag_count_raw = os.getenv("TAG_COUNT", "").strip()
    if not tag_count_raw:
        tag_count_raw = os.getenv("TAG_BUILD_TAG_LIMIT", "").strip()
    if tag_count_raw:
        try:
            tag_count = int(tag_count_raw)
        except ValueError as exc:
            raise RuntimeError("TAG_COUNT 必须是整数") from exc
    else:
        tag_count = 3
    if tag_count < 1:
        tag_count = 1
    if tag_count > 10:
        tag_count = 10

    return Config(
        bot_token=bot_token,
        db_path=db_path,
        allow_chat_ids=_parse_chat_ids(os.getenv("ALLOW_CHAT_IDS")),
        delete_duplicates=_parse_bool(os.getenv("DELETE_DUPLICATES"), default=False),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), default=True),
        keep_policy=keep_policy,
        retry_failed_deletes=_parse_bool(os.getenv("RETRY_FAILED_DELETES"), default=False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        tag_build_limit=tag_build_limit,
        tag_count=tag_count,
        tg_api_id=tg_api_id,
        tg_api_hash=os.getenv("TG_API_HASH", "").strip() or None,
        tg_session=tg_session,
    )
