"""AI request routing with retry and fallback."""

from __future__ import annotations

from typing import Any

from tg_media_dedupe_bot.db import Database


class AiAdapterProtocol:
    def generate(self, *, provider_key: str, model_key: str, prompt: str, timeout_ms: int) -> dict[str, Any]:
        raise NotImplementedError


def route_ai_request(
    *,
    db: Database,
    adapter: AiAdapterProtocol,
    provider_chain: list[str],
    model_key: str,
    prompt: str,
    retry_count: int,
    timeout_ms: int,
) -> dict[str, Any]:
    attempts = max(0, int(retry_count)) + 1
    last_error: Exception | None = None

    for provider_index, provider_key in enumerate(provider_chain):
        is_fallback = provider_index > 0
        for _ in range(attempts):
            try:
                payload = adapter.generate(
                    provider_key=provider_key,
                    model_key=model_key,
                    prompt=prompt,
                    timeout_ms=timeout_ms,
                )
                latency_ms = int(payload.get("latency_ms") or 0)
                db.record_ai_request_event(
                    provider_key=provider_key,
                    model_key=model_key,
                    success=True,
                    fallback_used=is_fallback,
                    downgrade_used=False,
                    latency_ms=latency_ms,
                )
                result = dict(payload)
                result["provider_key"] = provider_key
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                db.record_ai_request_event(
                    provider_key=provider_key,
                    model_key=model_key,
                    success=False,
                    fallback_used=is_fallback,
                    downgrade_used=False,
                    latency_ms=0,
                )

    if last_error is None:
        raise RuntimeError("provider_chain is empty")
    raise RuntimeError(f"all providers failed: {last_error}")
