from __future__ import annotations

import re
from typing import Any

HASHTAG_RE = re.compile(r"(?<!\w)#(\w{1,64})")
# Reject pure-numeric tags ("123") and mixed alphanumeric tags ("ab12", "4k", "a1").
# Kept in sync with telethon_tags._INVALID_TAG_RE.
INVALID_TAG_RE = re.compile(r"^(?:\d+|(?=.*\d)(?=.*[A-Za-z])[0-9A-Za-z]+)$")


def normalize_tag_text(tag: str) -> str:
    return str(tag or "").strip().lstrip("#").casefold()


def is_valid_tag(tag: str) -> bool:
    normalized = normalize_tag_text(tag)
    if not normalized:
        return False
    return INVALID_TAG_RE.match(normalized) is None


def dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        normalized = normalize_tag_text(tag)
        if not normalized or normalized in seen or not is_valid_tag(normalized):
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _extract_regex_tags(text: str) -> list[str]:
    return [m.group(1) for m in HASHTAG_RE.finditer(text or "")]


def extract_bot_hashtags(text: str, entities: list[Any] | None) -> list[str]:
    if not text:
        return []

    tags: list[str] = []
    for ent in entities or []:
        if str(getattr(ent, "type", "")).lower() != "hashtag":
            continue
        offset = int(getattr(ent, "offset", 0) or 0)
        length = int(getattr(ent, "length", 0) or 0)
        if length <= 1:
            continue
        raw = text[offset : offset + length]
        if raw.startswith("#") and len(raw) > 1:
            tags.append(raw[1:])

    tags.extend(_extract_regex_tags(text))
    return dedupe_tags(tags)


def extract_telethon_hashtags(text: str, entities: list[Any] | None) -> list[str]:
    if not text:
        return []

    tags: list[str] = []
    for ent in entities or []:
        if ent.__class__.__name__ != "MessageEntityHashtag":
            continue
        offset = int(getattr(ent, "offset", 0) or 0)
        length = int(getattr(ent, "length", 0) or 0)
        if length <= 1:
            continue
        raw = text[offset : offset + length]
        if raw.startswith("#") and len(raw) > 1:
            tags.append(raw[1:])

    tags.extend(_extract_regex_tags(text))
    return dedupe_tags(tags)
