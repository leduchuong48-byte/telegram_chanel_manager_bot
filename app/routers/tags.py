"""Tag groups management API."""

from pathlib import Path
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.models import MessageResponse

router = APIRouter(prefix="/api/tags", tags=["tags"])

# Use absolute paths based on repository root to avoid cwd issues.
_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"
_TAG_GROUPS_DIR = _DATA_DIR / "tag_groups"


class TagGroup(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


class TagGroupRequest(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)


def _ensure_dirs() -> None:
    _TAG_GROUPS_DIR.mkdir(parents=True, exist_ok=True)


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
