from __future__ import annotations

import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tg_media_dedupe_bot.models import MediaItem, ProcessDecision


@dataclass(frozen=True)
class DeletionRecord:
    result: str
    error: str | None
    attempted_at: int


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn = sqlite3.connect(self._path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")

        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_messages (
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  media_key TEXT NOT NULL,
                  media_type TEXT NOT NULL,
                  file_unique_id TEXT,
                  file_id TEXT,
                  message_date INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, message_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_messages_chat_media
                ON media_messages(chat_id, media_key, message_id);
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_canonical (
                  chat_id INTEGER NOT NULL,
                  media_key TEXT NOT NULL,
                  canonical_message_id INTEGER NOT NULL,
                  canonical_date INTEGER NOT NULL,
                  canonical_file_unique_id TEXT,
                  canonical_file_id TEXT,
                  canonical_media_type TEXT NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, media_key)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deletion_attempts (
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  media_key TEXT,
                  attempted_at INTEGER NOT NULL,
                  result TEXT NOT NULL,
                  error TEXT,
                  PRIMARY KEY(chat_id, message_id)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_deletions (
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  media_key TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, message_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_deletions_chat
                ON pending_deletions(chat_id, message_id);
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT NOT NULL PRIMARY KEY,
                  value TEXT NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                """
            )

            self._ensure_tag_library_schema()

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_aliases (
                  chat_id INTEGER NOT NULL,
                  old_tag TEXT NOT NULL,
                  new_tag TEXT NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, old_tag)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS text_block_keywords (
                  keyword TEXT NOT NULL PRIMARY KEY,
                  created_at INTEGER NOT NULL
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_build_sent (
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY(chat_id, message_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tag_build_sent_chat
                ON tag_build_sent(chat_id, message_id);
                """
            )

    def _ensure_tag_library_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(tag_library)").fetchall()
        columns = [str(row["name"]) for row in rows]
        if columns and "chat_id" not in columns:
            legacy_base = "tag_library_legacy"
            legacy_name = legacy_base
            suffix = 1
            while (
                self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (legacy_name,),
                ).fetchone()
                is not None
            ):
                legacy_name = f"{legacy_base}_{suffix}"
                suffix += 1
            self._conn.execute(f"ALTER TABLE tag_library RENAME TO {legacy_name}")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tag_library (
              chat_id INTEGER NOT NULL,
              tag TEXT NOT NULL,
              count INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(chat_id, tag)
            );
            """
        )

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        return tag.strip().lstrip("#").casefold()

    @staticmethod
    def _normalize_keyword(keyword: str) -> str:
        return keyword.strip().casefold()

    def record_tags(self, *, chat_id: int, tags: list[str]) -> None:
        normalized = [self._normalize_tag(tag) for tag in tags]
        normalized = [tag for tag in normalized if tag]
        if not normalized:
            return
        counts = Counter(normalized)
        now = int(time.time())
        with self._conn:
            for tag, inc in counts.items():
                self._conn.execute(
                    """
                    INSERT INTO tag_library(chat_id, tag, count, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(chat_id, tag) DO UPDATE SET
                      count=tag_library.count + excluded.count,
                      updated_at=excluded.updated_at
                    """,
                    (int(chat_id), tag, int(inc), now),
                )

    def merge_tag_counts(self, *, chat_id: int, old_tag: str, new_tag: str) -> None:
        old_key = self._normalize_tag(old_tag)
        new_key = self._normalize_tag(new_tag)
        if not old_key or not new_key or old_key == new_key:
            return
        row = self._conn.execute(
            "SELECT count FROM tag_library WHERE chat_id=? AND tag=?",
            (int(chat_id), old_key),
        ).fetchone()
        if row is None:
            return
        old_count = int(row["count"])
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tag_library(chat_id, tag, count, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(chat_id, tag) DO UPDATE SET
                  count=tag_library.count + excluded.count,
                  updated_at=excluded.updated_at
                """,
                (int(chat_id), new_key, old_count, now),
            )
            self._conn.execute(
                "DELETE FROM tag_library WHERE chat_id=? AND tag=?",
                (int(chat_id), old_key),
            )

    def list_tag_counts(self, *, chat_id: int) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            """
            SELECT tag, count
            FROM tag_library
            WHERE chat_id=?
            ORDER BY count DESC, tag ASC
            """,
            (int(chat_id),),
        ).fetchall()
        return [(str(row["tag"]), int(row["count"])) for row in rows]

    def list_tag_aliases(self, *, chat_id: int) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            """
            SELECT old_tag, new_tag
            FROM tag_aliases
            WHERE chat_id=?
            ORDER BY old_tag ASC
            """,
            (chat_id,),
        ).fetchall()
        return [(str(row["old_tag"]), str(row["new_tag"])) for row in rows]

    def set_tag_alias(self, *, chat_id: int, old_tag: str, new_tag: str) -> None:
        old_key = self._normalize_tag(old_tag)
        new_key = self._normalize_tag(new_tag)
        if not old_key or not new_key:
            return
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tag_aliases(chat_id, old_tag, new_tag, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(chat_id, old_tag) DO UPDATE SET
                  new_tag=excluded.new_tag,
                  updated_at=excluded.updated_at
                """,
                (chat_id, old_key, new_key, now),
            )

    def remove_tag_alias(self, *, chat_id: int, old_tag: str) -> bool:
        old_key = self._normalize_tag(old_tag)
        if not old_key:
            return False
        with self._conn:
            rowcount = self._conn.execute(
                "DELETE FROM tag_aliases WHERE chat_id=? AND old_tag=?",
                (chat_id, old_key),
            ).rowcount
        return rowcount > 0

    def add_text_block_keyword(self, keyword: str) -> None:
        normalized = self._normalize_keyword(keyword)
        if not normalized:
            return
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO text_block_keywords(keyword, created_at)
                VALUES(?, ?)
                """,
                (normalized, now),
            )

    def remove_text_block_keyword(self, keyword: str) -> bool:
        normalized = self._normalize_keyword(keyword)
        if not normalized:
            return False
        with self._conn:
            rowcount = self._conn.execute(
                "DELETE FROM text_block_keywords WHERE keyword=?",
                (normalized,),
            ).rowcount
        return rowcount > 0

    def list_text_block_keywords(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT keyword
            FROM text_block_keywords
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def add_tag_build_sent(self, *, chat_id: int, message_id: int) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO tag_build_sent(chat_id, message_id, created_at)
                VALUES(?, ?, ?)
                """,
                (chat_id, message_id, now),
            )

    def is_tag_build_sent(self, *, chat_id: int, message_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM tag_build_sent
            WHERE chat_id=? AND message_id=?
            """,
            (chat_id, message_id),
        ).fetchone()
        return row is not None

    def replace_message_id(self, *, chat_id: int, old_message_id: int, new_message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE media_messages SET message_id=? WHERE chat_id=? AND message_id=?",
                (new_message_id, chat_id, old_message_id),
            )
            self._conn.execute(
                "UPDATE media_canonical SET canonical_message_id=? WHERE chat_id=? AND canonical_message_id=?",
                (new_message_id, chat_id, old_message_id),
            )
            self._conn.execute(
                "UPDATE pending_deletions SET message_id=? WHERE chat_id=? AND message_id=?",
                (new_message_id, chat_id, old_message_id),
            )
            self._conn.execute(
                "UPDATE deletion_attempts SET message_id=? WHERE chat_id=? AND message_id=?",
                (new_message_id, chat_id, old_message_id),
            )

    def add_pending_deletion(self, *, chat_id: int, message_id: int, media_key: str, reason: str) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO pending_deletions(chat_id, message_id, media_key, reason, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (chat_id, message_id, media_key, reason, now),
            )

    def remove_pending_deletion(self, *, chat_id: int, message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM pending_deletions WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            )

    def list_pending_deletions(self, *, chat_id: int, limit: int) -> list[tuple[int, str, str]]:
        rows = self._conn.execute(
            """
            SELECT message_id, media_key, reason
            FROM pending_deletions
            WHERE chat_id=?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        return [(int(r["message_id"]), str(r["media_key"]), str(r["reason"])) for r in rows]

    def get_deletion_record(self, chat_id: int, message_id: int) -> DeletionRecord | None:
        row = self._conn.execute(
            "SELECT result, error, attempted_at FROM deletion_attempts WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return DeletionRecord(result=row["result"], error=row["error"], attempted_at=row["attempted_at"])

    def record_deletion_attempt(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        result: str,
        error: str | None,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO deletion_attempts(chat_id, message_id, media_key, attempted_at, result, error)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                  attempted_at=excluded.attempted_at,
                  result=excluded.result,
                  error=excluded.error,
                  media_key=COALESCE(excluded.media_key, deletion_attempts.media_key)
                """,
                (chat_id, message_id, media_key, now, result, error),
            )

    def process_media(self, item: MediaItem) -> ProcessDecision:
        now = int(time.time())

        for _ in range(3):
            with self._conn:
                inserted = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO media_messages(
                      chat_id, message_id, media_key, media_type,
                      file_unique_id, file_id, message_date, created_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.chat_id,
                        item.message_id,
                        item.media_key,
                        item.media_type,
                        item.file_unique_id,
                        item.file_id,
                        item.message_date,
                        now,
                    ),
                ).rowcount

                if inserted == 0:
                    canonical_id = self._get_canonical_message_id(item.chat_id, item.media_key) or item.message_id
                    return ProcessDecision(
                        already_processed=True,
                        canonical_message_id=canonical_id,
                        message_id_to_delete=None,
                        reason="already_processed",
                    )

                claimed = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO media_canonical(
                      chat_id, media_key,
                      canonical_message_id, canonical_date,
                      canonical_file_unique_id, canonical_file_id,
                      canonical_media_type, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.chat_id,
                        item.media_key,
                        item.message_id,
                        item.message_date,
                        item.file_unique_id,
                        item.file_id,
                        item.media_type,
                        now,
                    ),
                ).rowcount

                if claimed == 1:
                    return ProcessDecision(
                        already_processed=False,
                        canonical_message_id=item.message_id,
                        message_id_to_delete=None,
                        reason="canonical_claimed",
                    )

                canonical = self._conn.execute(
                    """
                    SELECT canonical_message_id, canonical_date
                    FROM media_canonical
                    WHERE chat_id=? AND media_key=?
                    """,
                    (item.chat_id, item.media_key),
                ).fetchone()

                if canonical is None:
                    continue

                canonical_id = int(canonical["canonical_message_id"])
                if item.message_id == canonical_id:
                    return ProcessDecision(
                        already_processed=False,
                        canonical_message_id=canonical_id,
                        message_id_to_delete=None,
                        reason="canonical_existing",
                    )

                if item.message_id > canonical_id:
                    return ProcessDecision(
                        already_processed=False,
                        canonical_message_id=canonical_id,
                        message_id_to_delete=item.message_id,
                        reason="duplicate_newer",
                    )

                updated = self._conn.execute(
                    """
                    UPDATE media_canonical
                    SET
                      canonical_message_id=?,
                      canonical_date=?,
                      canonical_file_unique_id=?,
                      canonical_file_id=?,
                      canonical_media_type=?,
                      updated_at=?
                    WHERE chat_id=? AND media_key=? AND canonical_message_id=?
                    """,
                    (
                        item.message_id,
                        item.message_date,
                        item.file_unique_id,
                        item.file_id,
                        item.media_type,
                        now,
                        item.chat_id,
                        item.media_key,
                        canonical_id,
                    ),
                ).rowcount

                if updated == 1:
                    return ProcessDecision(
                        already_processed=False,
                        canonical_message_id=item.message_id,
                        message_id_to_delete=canonical_id,
                        reason="duplicate_previous_canonical",
                    )

        canonical_id = self._get_canonical_message_id(item.chat_id, item.media_key) or item.message_id
        return ProcessDecision(
            already_processed=False,
            canonical_message_id=canonical_id,
            message_id_to_delete=None,
            reason="inconclusive",
        )

    def _get_canonical_message_id(self, chat_id: int, media_key: str) -> int | None:
        row = self._conn.execute(
            "SELECT canonical_message_id FROM media_canonical WHERE chat_id=? AND media_key=?",
            (chat_id, media_key),
        ).fetchone()
        if row is None:
            return None
        return int(row["canonical_message_id"])

    def get_chat_stats(self, chat_id: int) -> dict[str, int]:
        total = self._conn.execute(
            "SELECT COUNT(*) AS c FROM media_messages WHERE chat_id=?",
            (chat_id,),
        ).fetchone()["c"]
        unique_media = self._conn.execute(
            "SELECT COUNT(*) AS c FROM media_canonical WHERE chat_id=?",
            (chat_id,),
        ).fetchone()["c"]
        pending = self._conn.execute(
            "SELECT COUNT(*) AS c FROM pending_deletions WHERE chat_id=?",
            (chat_id,),
        ).fetchone()["c"]

        deleted_success = self._conn.execute(
            "SELECT COUNT(*) AS c FROM deletion_attempts WHERE chat_id=? AND result='success'",
            (chat_id,),
        ).fetchone()["c"]
        deleted_failed = self._conn.execute(
            "SELECT COUNT(*) AS c FROM deletion_attempts WHERE chat_id=? AND result='failed'",
            (chat_id,),
        ).fetchone()["c"]

        total_i = int(total)
        unique_i = int(unique_media)
        duplicates_found = total_i - unique_i
        if duplicates_found < 0:
            duplicates_found = 0

        return {
            "media_messages": total_i,
            "unique_media": unique_i,
            "duplicates_found": duplicates_found,
            "pending_deletions": int(pending),
            "deleted_success": int(deleted_success),
            "deleted_failed": int(deleted_failed),
        }
