"""Tag groups management API."""

from pathlib import Path
import json
import logging
import re
import sqlite3
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.models import MessageResponse

router = APIRouter(prefix="/api/tags", tags=["tags"])
logger = logging.getLogger(__name__)

# Use absolute paths based on repository root to avoid cwd issues.
_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"
_TAG_GROUPS_DIR = _DATA_DIR / "tag_groups"
_TAG_ALIASES_DIR = _DATA_DIR / "tag_aliases"


class TagGroup(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


class TagGroupRequest(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


class TagAliasRule(BaseModel):
    old_tag: str
    new_tag: str
    source: str | None = None


class TagAliasListResponse(BaseModel):
    scope: str
    chat_id: int | None = None
    count: int
    rules: list[TagAliasRule] = Field(default_factory=list)


class TagAliasUpsertRequest(BaseModel):
    old_tag: str
    new_tag: str
    scope: Literal["chat", "global"] = "chat"
    chat_id: int | None = None


def _ensure_dirs() -> None:
    _TAG_GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    _TAG_ALIASES_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="组名不能为空",
        )
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="组名包含非法字符",
        )
    return name


def _normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").casefold()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _parse_tags_from_text(content: str) -> list[str]:
    tags: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        if line.startswith("---") and line.endswith("---"):
            # Skip legacy group headers like -------组名--------
            continue
        parts = [part for part in re.split(r"[\s,]+", line) if part]
        for part in parts:
            normalized = _normalize_tag(part)
            if normalized:
                tags.append(normalized)
    return _dedupe_keep_order(tags)


def _normalize_scope(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"chat", "global", "effective"}:
        return value
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="scope 仅支持 chat/global/effective",
    )


def _alias_file_path(*, scope: str, chat_id: int | None) -> Path:
    if scope == "global":
        return _TAG_ALIASES_DIR / "global.txt"
    if chat_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat scope 需要 chat_id",
        )
    return _TAG_ALIASES_DIR / f"{int(chat_id)}.txt"


def _parse_alias_mapping(path: Path) -> dict[str, str]:
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
        old_tag = _normalize_tag(left)
        new_tag = _normalize_tag(right)
        if not old_tag or not new_tag or old_tag == new_tag:
            continue
        mapping[old_tag] = new_tag
    return mapping


def _write_alias_mapping(path: Path, mapping: dict[str, str]) -> None:
    lines: list[str] = []
    for old_tag in sorted(mapping.keys()):
        new_tag = mapping[old_tag]
        lines.append(f"#{old_tag}=#{new_tag}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _resolve_database_path() -> Path:
    default = _DATA_DIR / "bot.db"
    config_path = _BASE_DIR / "config.json"
    if not config_path.exists():
        return default
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    database = raw.get("database", {}) if isinstance(raw, dict) else {}
    if not isinstance(database, dict):
        return default
    value = str(database.get("path") or "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (_BASE_DIR / path).resolve()
    return path


def _sync_alias_mapping_to_db(*, chat_id: int, mapping: dict[str, str]) -> None:
    db_path = _resolve_database_path()
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        now = int(time.time())
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_aliases (
                  chat_id INTEGER NOT NULL,
                  old_tag TEXT NOT NULL,
                  new_tag TEXT NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, old_tag)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_library (
                  chat_id INTEGER NOT NULL,
                  tag TEXT NOT NULL,
                  count INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, tag)
                );
                """
            )
            rows = conn.execute(
                "SELECT old_tag, new_tag FROM tag_aliases WHERE chat_id=?",
                (int(chat_id),),
            ).fetchall()
            existing = {str(row["old_tag"]): str(row["new_tag"]) for row in rows}

            for old_tag, new_tag in mapping.items():
                if existing.get(old_tag) != new_tag:
                    conn.execute(
                        """
                        INSERT INTO tag_aliases(chat_id, old_tag, new_tag, updated_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(chat_id, old_tag) DO UPDATE SET
                          new_tag=excluded.new_tag,
                          updated_at=excluded.updated_at
                        """,
                        (int(chat_id), old_tag, new_tag, now),
                    )
                    row = conn.execute(
                        "SELECT count FROM tag_library WHERE chat_id=? AND tag=?",
                        (int(chat_id), old_tag),
                    ).fetchone()
                    if row is None:
                        continue
                    old_count = int(row["count"])
                    conn.execute(
                        """
                        INSERT INTO tag_library(chat_id, tag, count, updated_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(chat_id, tag) DO UPDATE SET
                          count=tag_library.count + excluded.count,
                          updated_at=excluded.updated_at
                        """,
                        (int(chat_id), new_tag, old_count, now),
                    )
                    conn.execute(
                        "DELETE FROM tag_library WHERE chat_id=? AND tag=?",
                        (int(chat_id), old_tag),
                    )

            for old_tag in existing:
                if old_tag in mapping:
                    continue
                conn.execute(
                    "DELETE FROM tag_aliases WHERE chat_id=? AND old_tag=?",
                    (int(chat_id), old_tag),
                )
    finally:
        conn.close()


@router.get("/groups", response_model=list[TagGroup])
async def list_groups(
    _: Annotated[str, Depends(get_current_user)],
) -> list[TagGroup]:
    """List tag groups from tag_groups directory."""
    _ensure_dirs()
    groups: list[TagGroup] = []
    for path in sorted(_TAG_GROUPS_DIR.glob("*.txt")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        tags = _parse_tags_from_text(content)
        groups.append(TagGroup(name=path.stem, tags=tags))
    return groups


@router.post("/groups", response_model=MessageResponse)
async def upsert_group(
    request: TagGroupRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Create or update a tag group file."""
    _ensure_dirs()
    name = _sanitize_name(request.name)

    normalized = [_normalize_tag(tag) for tag in request.tags]
    normalized = [tag for tag in normalized if tag]
    tags = _dedupe_keep_order(normalized)

    path = _TAG_GROUPS_DIR / f"{name}.txt"
    content = "\n".join(tags)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")

    return MessageResponse(success=True, message="标签组已保存")


@router.delete("/groups/{name}", response_model=MessageResponse)
async def delete_group(
    name: str,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Delete a tag group file."""
    _ensure_dirs()
    safe_name = _sanitize_name(name)
    path = _TAG_GROUPS_DIR / f"{safe_name}.txt"
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="标签组不存在",
        )
    path.unlink()
    return MessageResponse(success=True, message="标签组已删除")


@router.get("/aliases", response_model=TagAliasListResponse)
async def list_aliases(
    _: Annotated[str, Depends(get_current_user)],
    scope: str = "chat",
    chat_id: int | None = None,
) -> TagAliasListResponse:
    """List tag rename(alias) rules from files."""
    _ensure_dirs()
    safe_scope = _normalize_scope(scope)
    global_mapping = _parse_alias_mapping(_alias_file_path(scope="global", chat_id=None))

    if safe_scope == "global":
        rules = [TagAliasRule(old_tag=old, new_tag=global_mapping[old]) for old in sorted(global_mapping.keys())]
        return TagAliasListResponse(scope="global", chat_id=None, count=len(rules), rules=rules)

    chat_mapping = _parse_alias_mapping(_alias_file_path(scope="chat", chat_id=chat_id))
    if safe_scope == "chat":
        rules = [TagAliasRule(old_tag=old, new_tag=chat_mapping[old]) for old in sorted(chat_mapping.keys())]
        return TagAliasListResponse(scope="chat", chat_id=chat_id, count=len(rules), rules=rules)

    merged = {**chat_mapping, **global_mapping}
    rules: list[TagAliasRule] = []
    for old in sorted(merged.keys()):
        source = "global" if old in global_mapping else "chat"
        rules.append(TagAliasRule(old_tag=old, new_tag=merged[old], source=source))
    return TagAliasListResponse(scope="effective", chat_id=chat_id, count=len(rules), rules=rules)


@router.put("/aliases", response_model=MessageResponse)
async def upsert_alias(
    request: TagAliasUpsertRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Create or update a tag rename(alias) rule."""
    _ensure_dirs()
    safe_scope = _normalize_scope(request.scope)
    if safe_scope == "effective":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="effective scope 仅用于查看",
        )

    old_tag = _normalize_tag(request.old_tag)
    new_tag = _normalize_tag(request.new_tag)
    if not old_tag or not new_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="标签不能为空",
        )
    if old_tag == new_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧标签与新标签相同，无需设置",
        )

    path = _alias_file_path(scope=safe_scope, chat_id=request.chat_id)
    mapping = _parse_alias_mapping(path)
    mapping[old_tag] = new_tag
    _write_alias_mapping(path, mapping)
    if safe_scope == "chat" and request.chat_id is not None:
        try:
            _sync_alias_mapping_to_db(chat_id=int(request.chat_id), mapping=mapping)
        except sqlite3.OperationalError as exc:
            logger.warning("tag_alias_db_sync_failed chat=%s error=%s", request.chat_id, exc)
    return MessageResponse(success=True, message=f"已设置标签重命名：#{old_tag}=#{new_tag}")


@router.delete("/aliases", response_model=MessageResponse)
async def delete_alias(
    _: Annotated[str, Depends(get_current_user)],
    old_tag: str,
    scope: str = "chat",
    chat_id: int | None = None,
) -> MessageResponse:
    """Delete a tag rename(alias) rule."""
    _ensure_dirs()
    safe_scope = _normalize_scope(scope)
    if safe_scope == "effective":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="effective scope 仅用于查看",
        )

    old = _normalize_tag(old_tag)
    if not old:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="old_tag 不能为空",
        )

    path = _alias_file_path(scope=safe_scope, chat_id=chat_id)
    mapping = _parse_alias_mapping(path)
    if old not in mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="标签重命名规则不存在",
        )
    mapping.pop(old, None)
    _write_alias_mapping(path, mapping)
    if safe_scope == "chat" and chat_id is not None:
        try:
            _sync_alias_mapping_to_db(chat_id=int(chat_id), mapping=mapping)
        except sqlite3.OperationalError as exc:
            logger.warning("tag_alias_db_sync_failed chat=%s error=%s", chat_id, exc)
    return MessageResponse(success=True, message=f"已删除标签重命名：#{old}")
