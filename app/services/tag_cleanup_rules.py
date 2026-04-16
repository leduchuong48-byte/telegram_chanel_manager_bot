"""Rules for one-shot tag cleanup suggestions."""

from __future__ import annotations

from typing import Any

_ALLOWED_ACTIONS = {"merge", "rename", "deprecate", "categorize", "keep"}


def _normalize_tag(raw: str) -> str:
    value = str(raw or "").strip().lstrip("#").casefold()
    return value


def normalize_input_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in tags:
        tag = _normalize_tag(raw)
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def clean_suggestions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        source_tag = _normalize_tag(str(item.get("source_tag") or ""))
        if not source_tag:
            continue
        action = str(item.get("suggested_action") or "keep").strip().lower()
        if action not in _ALLOWED_ACTIONS:
            action = "keep"
        target_tag = _normalize_tag(str(item.get("suggested_target_tag") or ""))
        confidence_raw = item.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.0
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        if action in {"rename", "merge"}:
            if not target_tag or target_tag == source_tag:
                action = "keep"
                target_tag = ""

        cleaned.append(
            {
                **item,
                "source_tag": source_tag,
                "suggested_action": action,
                "suggested_target_tag": target_tag,
                "confidence": confidence,
            }
        )
    return cleaned
