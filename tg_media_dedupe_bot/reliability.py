from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelegramFailure:
    disposition: str
    category: str
    retry_after: int | None = None


def _retry_after_seconds(exc: BaseException) -> int | None:
    for attr in ("retry_after", "seconds"):
        value: Any = getattr(exc, attr, None)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def classify_delete_error(exc: BaseException) -> TelegramFailure:
    """Classify a Telegram deletion failure without making another API call."""
    text = str(exc or "").strip().lower()
    name = type(exc).__name__.lower()
    retry_after = _retry_after_seconds(exc)

    if "message can't be deleted" in text or "message cannot be deleted" in text:
        return TelegramFailure("terminal", "message_not_deletable")
    if "message to delete not found" in text or "message not found" in text:
        return TelegramFailure("terminal", "message_not_found")
    if "forbidden" in text or "not enough rights" in text or name == "forbidden":
        return TelegramFailure("terminal", "permission_denied")
    if retry_after is not None or "retry in" in text or "flood" in text or "rate limit" in text:
        return TelegramFailure("retryable", "rate_limited", retry_after=retry_after)
    if (
        isinstance(exc, (TimeoutError, OSError))
        or "timeout" in text
        or "timed out" in text
        or "connect" in text
        or "network" in text
        or "bad gateway" in text
        or "temporary failure" in text
        or "service unavailable" in text
        or name in {"networkerror", "timedout", "floodwaiterror"}
    ):
        category = "timeout" if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text else "upstream_unavailable"
        return TelegramFailure("retryable", category, retry_after=retry_after)

    return TelegramFailure("terminal", "delete_rejected")


def is_terminal_delete_error(error: str | None) -> bool:
    if not error:
        return False
    return classify_delete_error(RuntimeError(error)).disposition == "terminal"
