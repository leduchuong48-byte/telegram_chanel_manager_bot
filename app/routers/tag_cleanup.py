"""One-shot tag cleanup API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.services import tag_cleanup as tag_cleanup_service

router = APIRouter(prefix="/api/tag-cleanup", tags=["tag_cleanup"])

_config_manager: ConfigManager | None = None


class TagCleanupTagInput(BaseModel):
    tag: str
    count: int = 0
    samples: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class TagCleanupOptions(BaseModel):
    max_items: int = 500
    include_category_suggestion: bool = True


class TagCleanupPreviewRequest(BaseModel):
    source_type: str
    tags: list[TagCleanupTagInput] = Field(default_factory=list)
    options: TagCleanupOptions = Field(default_factory=TagCleanupOptions)


class TagCleanupSummary(BaseModel):
    total_input_tags: int
    total_suggestions: int
    high_confidence_count: int


class TagCleanupItem(BaseModel):
    item_id: str
    source_tag: str
    suggested_action: str
    suggested_target_tag: str | None = None
    suggested_category: str | None = None
    reason: str
    confidence: float
    samples: list[str] = Field(default_factory=list)
    decision: str = "pending"
    final_action: str | None = None
    final_target_tag: str | None = None
    final_category: str | None = None


class TagCleanupPreviewResponse(BaseModel):
    session_id: str
    status: str
    summary: TagCleanupSummary
    items: list[TagCleanupItem] = Field(default_factory=list)


class TagCleanupDecision(BaseModel):
    item_id: str
    decision: Literal["accept", "reject", "edit_accept", "pending"]
    final_action: str | None = None
    final_target_tag: str | None = None
    final_category: str | None = None


class TagCleanupApplyRequest(BaseModel):
    session_id: str
    decisions: list[TagCleanupDecision] = Field(default_factory=list)
    apply_mode: Literal["write", "dry_run"] = "dry_run"
    confirm_token: str | None = None


class TagCleanupApplySummary(BaseModel):
    accepted: int
    rejected: int
    edited: int
    merge_count: int
    rename_count: int
    deprecate_count: int
    categorize_count: int


class TagCleanupMappingItem(BaseModel):
    source_tag: str
    final_action: str
    final_target_tag: str = ""
    final_category: str | None = None


class TagCleanupApplyResponse(BaseModel):
    success: bool
    session_id: str
    status: str
    summary: TagCleanupApplySummary
    mapping: list[TagCleanupMappingItem] = Field(default_factory=list)


class TagCleanupExportRequest(BaseModel):
    session_id: str
    format: Literal["json", "csv"] = "json"
    export_type: Literal["suggestions", "final_mapping"] = "final_mapping"


class TagCleanupExportResponse(BaseModel):
    success: bool
    session_id: str
    format: str
    export_type: str
    filename: str
    content: list[dict]


class TagCleanupSessionSummary(BaseModel):
    total_input_tags: int
    total_suggestions: int
    high_confidence_count: int
    accepted: int
    rejected: int
    pending: int


class TagCleanupSessionResponse(BaseModel):
    session_id: str
    status: str
    summary: TagCleanupSessionSummary
    items: list[TagCleanupItem] = Field(default_factory=list)


def set_config_manager(manager: ConfigManager) -> None:
    global _config_manager
    _config_manager = manager


def _ensure_config_manager() -> None:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )


@router.post("/preview", response_model=TagCleanupPreviewResponse)
async def preview_cleanup(
    payload: TagCleanupPreviewRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupPreviewResponse:
    _ensure_config_manager()
    if not payload.tags:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cleanup_empty_input")
    raw = tag_cleanup_service.preview_cleanup(
        source_type=str(payload.source_type or "manual_input"),
        tag_items=[item.model_dump() for item in payload.tags],
    )
    return TagCleanupPreviewResponse(**raw)


@router.post("/apply", response_model=TagCleanupApplyResponse)
async def apply_cleanup(
    payload: TagCleanupApplyRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupApplyResponse:
    _ensure_config_manager()
    if payload.apply_mode == "write" and str(payload.confirm_token or "").strip().upper() != "APPLY":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cleanup_write_confirm_required")
    try:
        raw = tag_cleanup_service.apply_cleanup(
            session_id=payload.session_id,
            decisions=[item.model_dump() for item in payload.decisions],
            apply_mode=payload.apply_mode,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")
    return TagCleanupApplyResponse(**raw)


@router.post("/export", response_model=TagCleanupExportResponse)
async def export_cleanup(
    payload: TagCleanupExportRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupExportResponse:
    _ensure_config_manager()
    try:
        content = tag_cleanup_service.export_cleanup(
            session_id=payload.session_id,
            export_type=payload.export_type,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")

    filename = f"tag_cleanup_{payload.export_type}.{payload.format}"
    return TagCleanupExportResponse(
        success=True,
        session_id=payload.session_id,
        format=payload.format,
        export_type=payload.export_type,
        filename=filename,
        content=content,
    )


@router.get("/session/{session_id}", response_model=TagCleanupSessionResponse)
async def get_cleanup_session(
    session_id: str,
    _: Annotated[str, Depends(get_current_user)],
) -> TagCleanupSessionResponse:
    _ensure_config_manager()
    try:
        raw = tag_cleanup_service.get_cleanup_session(session_id=session_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cleanup_session_not_found")
    return TagCleanupSessionResponse(**raw)
