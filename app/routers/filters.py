"""Filters management API."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.models import MessageResponse

router = APIRouter(prefix="/api/filters", tags=["filters"])

# Use absolute paths based on repository root to avoid cwd issues.
_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"
_TEXT_BLOCK_DIR = _DATA_DIR / "text_block"
_TEXT_BLOCK_FILE = _TEXT_BLOCK_DIR / "global.txt"


class TextBlockResponse(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class TextBlockRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list)


def _ensure_dirs() -> None:
    _TEXT_BLOCK_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_keyword(keyword: str) -> str:
    return keyword.strip()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _parse_keywords(content: str) -> list[str]:
    keywords: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//") or line.startswith(";"):
            continue
        keywords.append(line)
    return _dedupe_keep_order(keywords)


@router.get("/text_block", response_model=TextBlockResponse)
async def get_text_block(
    _: Annotated[str, Depends(get_current_user)],
) -> TextBlockResponse:
    """Get global text block keywords."""
    if not _TEXT_BLOCK_FILE.exists():
        return TextBlockResponse(keywords=[])
    try:
        content = _TEXT_BLOCK_FILE.read_text(encoding="utf-8")
    except OSError:
        return TextBlockResponse(keywords=[])
    return TextBlockResponse(keywords=_parse_keywords(content))


@router.post("/text_block", response_model=MessageResponse)
async def update_text_block(
    request: TextBlockRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Update global text block keywords."""
    _ensure_dirs()
    normalized = [_normalize_keyword(item) for item in request.keywords]
    normalized = [item for item in normalized if item]
    # Drop comment-like lines to match runtime parser behavior.
    filtered = [
        item for item in normalized
        if not (item.startswith("#") or item.startswith("//") or item.startswith(";"))
    ]
    keywords = _dedupe_keep_order(filtered)

    content = "\n".join(keywords)
    if content:
        content += "\n"
    _TEXT_BLOCK_FILE.write_text(content, encoding="utf-8")

    return MessageResponse(success=True, message="屏蔽词已保存")
