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


class TagSection(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


class TagGroup(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    sections: list[TagSection] = Field(default_factory=list)


class TagGroupRequest(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    sections: list[TagSection] = Field(default_factory=list)


class TagGroupRenameRequest(BaseModel):
    old_name: str
    new_name: str


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


class TagPreviewResponse(BaseModel):
    group: str
    target: str | None = None
    sections: list[TagSection] = Field(default_factory=list)
    rendered_messages: list[str] = Field(default_factory=list)
    applied_alias_count: int = 0
    split_count: int = 0


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


def _extract_section_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("---") or not stripped.endswith("---"):
        return None
    core = stripped.strip("-").strip().lstrip("#").strip()
    return core or None


def parse_tag_group_sections(content: str) -> list[dict[str, list[str] | str]]:
    sections: list[dict[str, list[str] | str]] = []
    current_name = "未分组"
    current_tags: list[str] = []

    def flush() -> None:
        nonlocal current_tags, current_name
        deduped = _dedupe_keep_order([tag for tag in current_tags if tag])
        if sections or deduped or current_name != "未分组":
            sections.append({"name": current_name, "tags": deduped})
        current_tags = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        section_name = _extract_section_name(line)
        if section_name is not None:
            flush()
            current_name = section_name
            continue
        parts = [part for part in re.split(r"[\s,]+", line) if part]
        for part in parts:
            normalized = _normalize_tag(part)
            if normalized:
                current_tags.append(normalized)

    flush()
    if not sections:
        sections.append({"name": "未分组", "tags": []})
    return [section for section in sections if section.get("name")]


def dump_tag_group_sections(sections: list[dict[str, list[str] | str]] | list[TagSection]) -> str:
    lines: list[str] = []
    normalized_sections: list[tuple[str, list[str]]] = []
    for section in sections:
        if isinstance(section, TagSection):
            name = section.name
            tags = section.tags
        else:
            name = str(section.get("name") or "").strip()  # type: ignore[union-attr]
            tags = list(section.get("tags") or [])  # type: ignore[union-attr]
        safe_name = name or "未分组"
        normalized_tags = _dedupe_keep_order([_normalize_tag(tag) for tag in tags if _normalize_tag(tag)])
        normalized_sections.append((safe_name, normalized_tags))

    for index, (name, tags) in enumerate(normalized_sections):
        if index > 0:
            lines.append("")
        lines.append(f"-------{name}--------")
        for tag in tags:
            lines.append(f"#{tag}")
    return "\n".join(lines).rstrip() + "\n"


def _sections_to_models(sections: list[dict[str, list[str] | str]]) -> list[TagSection]:
    models: list[TagSection] = []
    for section in sections:
        name = str(section.get("name") or "未分组")
        tags = [str(tag) for tag in (section.get("tags") or [])]
        models.append(TagSection(name=name, tags=tags))
    return models


def _build_preview_messages(sections: list[TagSection], alias_mapping: dict[str, str] | None = None) -> tuple[list[str], int]:
    alias_mapping = alias_mapping or {}
    rendered_sections: list[str] = []
    applied_alias_count = 0
    for section in sections:
        lines = [f"-------{section.name}--------"]
        section_tags: list[str] = []
        for tag in section.tags:
            mapped = alias_mapping.get(tag, tag)
            if mapped != tag:
                applied_alias_count += 1
            section_tags.append(f"#{mapped}")
        if section_tags:
            lines.append(" ".join(section_tags))
        rendered_sections.append("\n".join(lines))
    return rendered_sections, applied_alias_count


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
        sections = _sections_to_models(parse_tag_group_sections(content))
        groups.append(TagGroup(name=path.stem, tags=tags, sections=sections))
    return groups


@router.post("/groups", response_model=MessageResponse)
async def upsert_group(
    request: TagGroupRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Create or update a tag group file."""
    _ensure_dirs()
    name = _sanitize_name(request.name)

    if request.sections:
        content = dump_tag_group_sections(request.sections)
        flat_tags: list[str] = []
        for section in request.sections:
            flat_tags.extend(section.tags)
        tags = _dedupe_keep_order([_normalize_tag(tag) for tag in flat_tags if _normalize_tag(tag)])
    else:
        normalized = [_normalize_tag(tag) for tag in request.tags]
        normalized = [tag for tag in normalized if tag]
        tags = _dedupe_keep_order(normalized)
        content = "\n".join(tags)
        if content:
            content += "\n"

    path = _TAG_GROUPS_DIR / f"{name}.txt"
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


@router.post("/groups/rename", response_model=MessageResponse)
async def rename_group(
    request: TagGroupRenameRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Rename a tag group file atomically."""
    _ensure_dirs()
    old_name = _sanitize_name(request.old_name)
    new_name = _sanitize_name(request.new_name)
    if old_name == new_name:
        return MessageResponse(success=True, message="标签组名称未变化")

    old_path = _TAG_GROUPS_DIR / f"{old_name}.txt"
    new_path = _TAG_GROUPS_DIR / f"{new_name}.txt"
    if not old_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="原标签组不存在",
        )
    if new_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="新标签组名称已存在",
        )

    old_path.rename(new_path)
    return MessageResponse(success=True, message="标签组已重命名")


@router.get("/preview", response_model=TagPreviewResponse)
async def preview_group(
    group: str,
    target: str | None,
    _: Annotated[str, Depends(get_current_user)],
) -> dict[str, object]:
    """Preview rendered tag group output with alias mapping applied."""
    _ensure_dirs()
    safe_group = _sanitize_name(group)
    path = _TAG_GROUPS_DIR / f"{safe_group}.txt"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="标签组不存在")
    content = path.read_text(encoding="utf-8")
    sections = _sections_to_models(parse_tag_group_sections(content))

    alias_mapping: dict[str, str] = {}
    global_path = _alias_file_path(scope="global", chat_id=None)
    alias_mapping.update(_parse_alias_mapping(global_path))
    target_token = str(target or "").strip()
    if target_token:
        try:
            chat_id = int(target_token)
        except ValueError:
            chat_id = None
        if chat_id is not None:
            alias_mapping.update(_parse_alias_mapping(_alias_file_path(scope="chat", chat_id=chat_id)))

    rendered_messages, applied_alias_count = _build_preview_messages(sections, alias_mapping)
    return {
        "group": safe_group,
        "target": target_token or None,
        "sections": [section.model_dump() for section in sections],
        "rendered_messages": rendered_messages,
        "applied_alias_count": applied_alias_count,
        "split_count": len(rendered_messages),
    }


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
