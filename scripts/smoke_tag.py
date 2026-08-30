#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, "/app")


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: actual={actual!r} expected={expected!r}")


def _report_failure(exc: BaseException) -> None:
    tb = traceback.extract_tb(exc.__traceback__)
    last = tb[-1] if tb else None
    if last is not None:
        print(f"TRACE {last.filename}:{last.lineno} in {last.name}")
    print(f"ERROR {exc}")


def main() -> int:
    if not os.getenv("TG_BOT_TOKEN"):
        print("WARN TG_BOT_TOKEN missing (not required for this smoke test)")

    from tg_media_dedupe_bot.telegram_bot import (
        _apply_tag_aliases,
        _build_tag_caption,
        _extract_hashtags_bot,
    )
    from tg_media_dedupe_bot.telethon_tags import _extract_hashtags as _extract_hashtags_telethon

    legacy_text = "hello #Tag #tag #TAG"
    legacy_tags = _extract_hashtags_bot(legacy_text, None)
    legacy_mapped = _apply_tag_aliases(legacy_tags, {})
    _assert_equal(legacy_mapped, ["tag"], "legacy_dedupe")
    legacy_caption = _build_tag_caption("hello", legacy_mapped)
    _assert_equal(legacy_caption, "hello\n\n#tag", "legacy_caption")

    alias_text = "hi #Old #old2 #OLD2"
    alias_tags = _extract_hashtags_bot(alias_text, None)
    alias_mapped = _apply_tag_aliases(alias_tags, {"old": "new", "old2": "new"})
    _assert_equal(alias_mapped, ["new"], "alias_merge_dedupe")

    order_text = "#b #a #b #c"
    order_tags = _extract_hashtags_bot(order_text, None)
    order_mapped = _apply_tag_aliases(order_tags, {})
    _assert_equal(order_mapped, ["b", "a", "c"], "order_stable")

    invalid_text = "#123 #ab12 #abc"
    invalid_tags = _extract_hashtags_bot(invalid_text, None)
    invalid_mapped = _apply_tag_aliases(invalid_tags, {})
    _assert_equal(invalid_mapped, ["abc"], "invalid_filtered")

    _assert_equal(_extract_hashtags_bot("", None), [], "empty_text")
    _assert_equal(_build_tag_caption("", ["tag"]), "#tag", "caption_only_tags")
    _assert_equal(_build_tag_caption("hello", []), "hello", "caption_only_text")

    telethon_tags = _extract_hashtags_telethon("#TeSt #test #TEST #123 #a1", None)
    _assert_equal(telethon_tags, ["test"], "telethon_dedupe")

    print("smoke_tag ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        _report_failure(exc)
        raise
