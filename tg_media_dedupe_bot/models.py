from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaItem:
    chat_id: int
    message_id: int
    media_key: str
    media_type: str
    file_unique_id: str | None
    file_id: str | None
    message_date: int
    is_forwarded: bool = False


@dataclass(frozen=True)
class ProcessDecision:
    already_processed: bool
    canonical_message_id: int
    message_id_to_delete: int | None
    reason: str
