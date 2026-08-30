from __future__ import annotations

import os
from pathlib import Path


def app_root() -> Path:
    raw = os.getenv("APP_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def resolve_app_path(value: str | Path, *, default: str | Path | None = None) -> Path:
    raw = str(value or default or "").strip()
    if not raw:
        raw = str(default or "")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = app_root() / path
    return path.resolve()


def telethon_session_db_path(value: str | Path) -> Path:
    path = resolve_app_path(value)
    if path.suffix:
        return path
    return path.with_suffix(".session")
