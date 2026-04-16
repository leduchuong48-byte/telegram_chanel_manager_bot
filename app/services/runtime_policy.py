"""Runtime policy resolver for AI responses mode."""

from __future__ import annotations

_ALLOWED = {"off", "auto", "force_on", "force_off"}


def _normalize(mode: str | None) -> str | None:
    if mode is None:
        return None
    value = str(mode).strip()
    if not value:
        return None
    if value not in _ALLOWED:
        raise ValueError(f"unsupported_mode:{value}")
    return value


def resolve_responses_mode(
    *,
    request_mode: str | None,
    model_mode: str | None,
    provider_mode: str | None,
    global_mode: str,
) -> str:
    request_norm = _normalize(request_mode)
    if request_norm is not None:
        return request_norm
    model_norm = _normalize(model_mode)
    if model_norm is not None:
        return model_norm
    provider_norm = _normalize(provider_mode)
    if provider_norm is not None:
        return provider_norm
    global_norm = _normalize(global_mode)
    if global_norm is None:
        raise ValueError('unsupported_mode:')
    return global_norm
