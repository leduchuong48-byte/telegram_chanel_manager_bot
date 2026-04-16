"""AI health and metrics summary API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from tg_media_dedupe_bot.db import Database

router = APIRouter(prefix="/api/ai", tags=["ai_health"])

_config_manager: ConfigManager | None = None


class AiHealthSummary(BaseModel):
    providers_total: int
    providers_healthy: int
    providers_degraded: int
    models_total: int
    models_available: int
    sync_jobs_running: int
    classification_jobs_running: int
    review_queue_pending: int
    fallback_rate_1h: float
    responses_usage_rate_1h: float


class ProviderMetric(BaseModel):
    provider_key: str
    request_count: int
    success_rate: float
    avg_latency_ms: int
    p95_latency_ms: int
    fallback_count: int
    downgrade_count: int


class ModelMetric(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model_key: str
    request_count: int
    success_rate: float
    structured_output_success_rate: float


class WorkflowMetric(BaseModel):
    preview_requests: int
    batch_jobs: int
    review_pending: int
    review_approved_rate: float
    review_rejected_rate: float


class AiMetricsSummary(BaseModel):
    window: str
    providers: list[ProviderMetric]
    models: list[ModelMetric]
    workflow: WorkflowMetric



def set_config_manager(manager: ConfigManager) -> None:
    global _config_manager
    _config_manager = manager



def _get_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager



def _resolve_db_path(config: dict) -> Path:
    db_cfg = config.get("database", {}) if isinstance(config, dict) else {}
    if not isinstance(db_cfg, dict):
        db_cfg = {}
    raw = str(db_cfg.get("path") or "./data/bot.db").strip() or "./data/bot.db"
    return Path(raw).expanduser()



def _db(config: dict) -> Database:
    return Database(_resolve_db_path(config))


@router.get('/health', response_model=AiHealthSummary)
async def get_ai_health(_: Annotated[str, Depends(get_current_user)]) -> AiHealthSummary:
    manager = _get_config_manager()
    db = _db(manager.get_config())
    try:
        providers = db.list_providers(enabled_only=False)
        models = db.list_models(provider_key=None, enabled_only=False)
    finally:
        db.close()

    providers_total = len(providers)
    providers_healthy = len([item for item in providers if bool(item.get('enabled'))])
    providers_degraded = providers_total - providers_healthy
    models_total = len(models)
    models_available = len([item for item in models if bool(item.get('enabled'))])
    return AiHealthSummary(
        providers_total=providers_total,
        providers_healthy=providers_healthy,
        providers_degraded=providers_degraded,
        models_total=models_total,
        models_available=models_available,
        sync_jobs_running=0,
        classification_jobs_running=0,
        review_queue_pending=0,
        fallback_rate_1h=0.0,
        responses_usage_rate_1h=0.0,
    )


@router.get('/metrics-summary', response_model=AiMetricsSummary)
async def get_ai_metrics_summary(
    _: Annotated[str, Depends(get_current_user)],
    window: Literal['1h', '24h'] = '1h',
) -> AiMetricsSummary:
    manager = _get_config_manager()
    window_seconds = 3600 if window == '1h' else 86400
    since_ts = int(time.time()) - window_seconds

    db = _db(manager.get_config())
    try:
        providers = db.list_providers(enabled_only=False)
        models = db.list_models(provider_key=None, enabled_only=False)
        provider_event_metrics = db.get_ai_request_metrics_by_provider(since_ts=since_ts)
        model_event_metrics = db.get_ai_request_metrics_by_model(since_ts=since_ts)
    finally:
        db.close()

    provider_metrics: list[ProviderMetric] = []
    for provider in providers:
        provider_key = str(provider.get('provider_key') or '')
        metrics = provider_event_metrics.get(provider_key, None)
        if metrics is None:
            request_count = 0
            success_rate = 1.0 if bool(provider.get('enabled')) else 0.0
            avg_latency_ms = 0
            p95_latency_ms = 0
            fallback_count = 0
            downgrade_count = 0
        else:
            request_count = int(metrics.get('request_count') or 0)
            success_rate = float(metrics.get('success_rate') or 0.0)
            avg_latency_ms = int(metrics.get('avg_latency_ms') or 0)
            p95_latency_ms = int(metrics.get('p95_latency_ms') or avg_latency_ms)
            p95_latency_ms = int(metrics.get('p95_latency_ms') or avg_latency_ms)
            fallback_count = int(metrics.get('fallback_count') or 0)
            downgrade_count = int(metrics.get('downgrade_count') or 0)
        provider_metrics.append(
            ProviderMetric(
                provider_key=provider_key,
                request_count=request_count,
                success_rate=success_rate,
                avg_latency_ms=avg_latency_ms,
                p95_latency_ms=p95_latency_ms,
                fallback_count=fallback_count,
                downgrade_count=downgrade_count,
            )
        )

    model_metrics: list[ModelMetric] = []
    for model in models:
        model_key = f"{str(model.get('provider_key') or '')}:{str(model.get('model_id') or '')}"
        metrics = model_event_metrics.get(model_key, None)
        if metrics is None:
            request_count = 0
            success_rate = 1.0 if bool(model.get('enabled')) else 0.0
        else:
            request_count = int(metrics.get('request_count') or 0)
            success_rate = float(metrics.get('success_rate') or 0.0)
        model_metrics.append(
            ModelMetric(
                model_key=model_key,
                request_count=request_count,
                success_rate=success_rate,
                structured_output_success_rate=0.0,
            )
        )

    return AiMetricsSummary(
        window=window,
        providers=provider_metrics,
        models=model_metrics,
        workflow=WorkflowMetric(
            preview_requests=0,
            batch_jobs=0,
            review_pending=0,
            review_approved_rate=0.0,
            review_rejected_rate=0.0,
        ),
    )
