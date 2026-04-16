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
                CREATE TABLE IF NOT EXISTS deletion_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  event_type TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  result TEXT NOT NULL,
                  detail TEXT,
                  created_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_deletion_events_chat_created
                ON deletion_events(chat_id, created_at DESC);
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

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS providers (
                  provider_key TEXT NOT NULL PRIMARY KEY,
                  display_name TEXT NOT NULL,
                  provider_type TEXT NOT NULL,
                  base_url TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  use_responses_mode TEXT NOT NULL DEFAULT 'auto',
                  default_model TEXT NOT NULL DEFAULT '',
                  last_test_status TEXT NOT NULL DEFAULT '',
                  last_test_at INTEGER NOT NULL DEFAULT 0,
                  last_probe_status TEXT NOT NULL DEFAULT '',
                  last_probe_at INTEGER NOT NULL DEFAULT 0,
                  supports_responses INTEGER NOT NULL DEFAULT 0,
                  capabilities_json TEXT NOT NULL DEFAULT '{}',
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_providers_enabled_updated
                ON providers(enabled, updated_at DESC, provider_key ASC);
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS models (
                  provider_key TEXT NOT NULL,
                  model_id TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  source TEXT NOT NULL DEFAULT 'sync',
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(provider_key, model_id),
                  FOREIGN KEY(provider_key) REFERENCES providers(provider_key) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_models_enabled_updated
                ON models(enabled, updated_at DESC, provider_key ASC, model_id ASC);
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_sync_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  trigger_source TEXT NOT NULL,
                  synced_count INTEGER NOT NULL DEFAULT 0,
                  created_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_request_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  provider_key TEXT NOT NULL,
                  model_key TEXT NOT NULL,
                  success INTEGER NOT NULL DEFAULT 0,
                  fallback_used INTEGER NOT NULL DEFAULT 0,
                  downgrade_used INTEGER NOT NULL DEFAULT 0,
                  latency_ms INTEGER NOT NULL DEFAULT 0,
                  created_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_request_events_created
                ON ai_request_events(created_at DESC);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_request_events_provider_created
                ON ai_request_events(provider_key, created_at DESC);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_request_events_model_created
                ON ai_request_events(model_key, created_at DESC);
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_controllers (
                  user_id INTEGER NOT NULL PRIMARY KEY,
                  display_name TEXT NOT NULL DEFAULT '',
                  role TEXT NOT NULL DEFAULT 'operator',
                  enabled INTEGER NOT NULL DEFAULT 1,
                  is_primary INTEGER NOT NULL DEFAULT 0,
                  source TEXT NOT NULL DEFAULT '',
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  last_verified_at INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_controllers_primary_enabled
                ON telegram_controllers(is_primary, enabled, updated_at DESC);
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                  job_id TEXT NOT NULL PRIMARY KEY,
                  chat_id INTEGER NOT NULL,
                  task_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL,
                  priority INTEGER NOT NULL DEFAULT 0,
                  created_at INTEGER NOT NULL,
                  started_at INTEGER NOT NULL DEFAULT 0,
                  finished_at INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT,
                  scanned INTEGER NOT NULL DEFAULT 0,
                  matched INTEGER NOT NULL DEFAULT 0,
                  acted INTEGER NOT NULL DEFAULT 0,
                  failed INTEGER NOT NULL DEFAULT 0,
                  attempt_count INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 3,
                  next_run_at INTEGER NOT NULL DEFAULT 0,
                  worker_id TEXT NOT NULL DEFAULT '',
                  lease_expires_at INTEGER NOT NULL DEFAULT 0,
                  last_heartbeat_at INTEGER NOT NULL DEFAULT 0,
                  terminal_reason TEXT NOT NULL DEFAULT '',
                  retryable_class TEXT NOT NULL DEFAULT '',
                  submitted_by TEXT NOT NULL DEFAULT '',
                  session_snapshot_json TEXT NOT NULL DEFAULT '{}',
                  target_snapshot_json TEXT NOT NULL DEFAULT '{}',
                  policy_snapshot_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  event_payload_json TEXT NOT NULL DEFAULT '{}',
                  created_at INTEGER NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_job_events_job_created
                ON job_events(job_id, created_at DESC, id DESC);
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_locks (
                  lock_key TEXT NOT NULL PRIMARY KEY,
                  job_id TEXT NOT NULL,
                  worker_id TEXT NOT NULL DEFAULT '',
                  lease_expires_at INTEGER NOT NULL DEFAULT 0,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letter_actions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  action TEXT NOT NULL,
                  actor TEXT NOT NULL DEFAULT '',
                  note TEXT NOT NULL DEFAULT '',
                  created_at INTEGER NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dead_letter_actions_job_created
                ON dead_letter_actions(job_id, created_at DESC, id DESC);
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_checkpoints (
                  job_id TEXT NOT NULL,
                  stage TEXT NOT NULL,
                  cursor_json TEXT NOT NULL,
                  updated_at INTEGER NOT NULL,
                  PRIMARY KEY(job_id, stage),
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_actions (
                  idempotency_key TEXT NOT NULL PRIMARY KEY,
                  job_id TEXT NOT NULL,
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  action TEXT NOT NULL,
                  status TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 1,
                  error TEXT,
                  updated_at INTEGER NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_job_actions_job_status
                ON job_actions(job_id, status, updated_at DESC);
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

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS managed_chats (
                  chat_id INTEGER NOT NULL PRIMARY KEY,
                  title TEXT NOT NULL DEFAULT '',
                  username TEXT NOT NULL DEFAULT '',
                  chat_type TEXT NOT NULL DEFAULT '',
                  source TEXT NOT NULL DEFAULT '',
                  is_active INTEGER NOT NULL DEFAULT 1,
                  bot_status TEXT NOT NULL DEFAULT 'unknown',
                  bot_can_manage INTEGER NOT NULL DEFAULT 0,
                  last_seen_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  verified_at INTEGER NOT NULL DEFAULT 0,
                  verified_by TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_managed_chats_active_updated
                ON managed_chats(is_active, updated_at DESC);
                """
            )
            self._ensure_managed_chats_schema()
            self._ensure_telegram_controllers_schema()
            self._ensure_job_progress_schema()
            self._ensure_cleaner_control_plane_schema()
            self._ensure_provider_registry_schema()

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

    def _ensure_job_progress_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        if not rows:
            return
        columns = {str(row["name"]) for row in rows}
        migrations: list[tuple[str, str]] = [
            ("scanned", "ALTER TABLE jobs ADD COLUMN scanned INTEGER NOT NULL DEFAULT 0"),
            ("matched", "ALTER TABLE jobs ADD COLUMN matched INTEGER NOT NULL DEFAULT 0"),
            ("acted", "ALTER TABLE jobs ADD COLUMN acted INTEGER NOT NULL DEFAULT 0"),
            ("failed", "ALTER TABLE jobs ADD COLUMN failed INTEGER NOT NULL DEFAULT 0"),
        ]
        for column, sql in migrations:
            if column in columns:
                continue
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


    def _ensure_cleaner_control_plane_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        if not rows:
            return
        columns = {str(row["name"]) for row in rows}
        migrations: list[tuple[str, str]] = [
            ("attempt_count", "ALTER TABLE jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3"),
            ("next_run_at", "ALTER TABLE jobs ADD COLUMN next_run_at INTEGER NOT NULL DEFAULT 0"),
            ("worker_id", "ALTER TABLE jobs ADD COLUMN worker_id TEXT NOT NULL DEFAULT ''"),
            ("lease_expires_at", "ALTER TABLE jobs ADD COLUMN lease_expires_at INTEGER NOT NULL DEFAULT 0"),
            ("last_heartbeat_at", "ALTER TABLE jobs ADD COLUMN last_heartbeat_at INTEGER NOT NULL DEFAULT 0"),
            ("terminal_reason", "ALTER TABLE jobs ADD COLUMN terminal_reason TEXT NOT NULL DEFAULT ''"),
            ("retryable_class", "ALTER TABLE jobs ADD COLUMN retryable_class TEXT NOT NULL DEFAULT ''"),
            ("submitted_by", "ALTER TABLE jobs ADD COLUMN submitted_by TEXT NOT NULL DEFAULT ''"),
            ("session_snapshot_json", "ALTER TABLE jobs ADD COLUMN session_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
            ("target_snapshot_json", "ALTER TABLE jobs ADD COLUMN target_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
            ("policy_snapshot_json", "ALTER TABLE jobs ADD COLUMN policy_snapshot_json TEXT NOT NULL DEFAULT '{}'"),
        ]
        for column, sql in migrations:
            if column in columns:
                continue
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              event_payload_json TEXT NOT NULL DEFAULT '{}',
              created_at INTEGER NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_job_events_job_created
            ON job_events(job_id, created_at DESC, id DESC);
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS target_locks (
              lock_key TEXT NOT NULL PRIMARY KEY,
              job_id TEXT NOT NULL,
              worker_id TEXT NOT NULL DEFAULT '',
              lease_expires_at INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              action TEXT NOT NULL,
              actor TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            );
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dead_letter_actions_job_created
            ON dead_letter_actions(job_id, created_at DESC, id DESC);
            """
        )



    def _ensure_provider_registry_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(providers)").fetchall()
        if not rows:
            return
        columns = {str(row["name"]) for row in rows}
        migrations: list[tuple[str, str]] = [
            ("use_responses_mode", "ALTER TABLE providers ADD COLUMN use_responses_mode TEXT NOT NULL DEFAULT 'auto'"),
            ("default_model", "ALTER TABLE providers ADD COLUMN default_model TEXT NOT NULL DEFAULT ''"),
            ("last_test_status", "ALTER TABLE providers ADD COLUMN last_test_status TEXT NOT NULL DEFAULT ''"),
            ("last_test_at", "ALTER TABLE providers ADD COLUMN last_test_at INTEGER NOT NULL DEFAULT 0"),
            ("last_probe_status", "ALTER TABLE providers ADD COLUMN last_probe_status TEXT NOT NULL DEFAULT ''"),
            ("last_probe_at", "ALTER TABLE providers ADD COLUMN last_probe_at INTEGER NOT NULL DEFAULT 0"),
            ("supports_responses", "ALTER TABLE providers ADD COLUMN supports_responses INTEGER NOT NULL DEFAULT 0"),
            ("capabilities_json", "ALTER TABLE providers ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'"),
        ]
        for column, sql in migrations:
            if column in columns:
                continue
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _ensure_managed_chats_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(managed_chats)").fetchall()
        if not rows:
            return
        columns = {str(row["name"]) for row in rows}
        migrations: list[tuple[str, str]] = [
            ("source", "ALTER TABLE managed_chats ADD COLUMN source TEXT NOT NULL DEFAULT ''"),
            ("bot_status", "ALTER TABLE managed_chats ADD COLUMN bot_status TEXT NOT NULL DEFAULT 'unknown'"),
            ("bot_can_manage", "ALTER TABLE managed_chats ADD COLUMN bot_can_manage INTEGER NOT NULL DEFAULT 0"),
            ("last_seen_at", "ALTER TABLE managed_chats ADD COLUMN last_seen_at INTEGER NOT NULL DEFAULT 0"),
            ("updated_at", "ALTER TABLE managed_chats ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
            ("verified_at", "ALTER TABLE managed_chats ADD COLUMN verified_at INTEGER NOT NULL DEFAULT 0"),
            ("verified_by", "ALTER TABLE managed_chats ADD COLUMN verified_by TEXT NOT NULL DEFAULT ''"),
        ]
        for column, sql in migrations:
            if column in columns:
                continue
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _ensure_telegram_controllers_schema(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(telegram_controllers)").fetchall()
        if not rows:
            return
        columns = {str(row["name"]) for row in rows}
        migrations: list[tuple[str, str]] = [
            ("display_name", "ALTER TABLE telegram_controllers ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"),
            ("role", "ALTER TABLE telegram_controllers ADD COLUMN role TEXT NOT NULL DEFAULT 'operator'"),
            ("enabled", "ALTER TABLE telegram_controllers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),
            ("is_primary", "ALTER TABLE telegram_controllers ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0"),
            ("source", "ALTER TABLE telegram_controllers ADD COLUMN source TEXT NOT NULL DEFAULT ''"),
            ("created_at", "ALTER TABLE telegram_controllers ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0"),
            ("updated_at", "ALTER TABLE telegram_controllers ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
            ("last_verified_at", "ALTER TABLE telegram_controllers ADD COLUMN last_verified_at INTEGER NOT NULL DEFAULT 0"),
        ]
        for column, sql in migrations:
            if column not in columns:
                self._conn.execute(sql)

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

    def upsert_managed_chat(
        self,
        *,
        chat_id: int,
        title: str,
        username: str,
        chat_type: str,
        source: str,
        is_active: bool,
        bot_status: str | None = None,
        bot_can_manage: bool | None = None,
        verified_at: int | None = None,
        verified_by: str | None = None,
    ) -> None:
        now = int(time.time())
        existing = self._conn.execute(
            "SELECT bot_status, bot_can_manage, verified_at, verified_by FROM managed_chats WHERE chat_id=?",
            (int(chat_id),),
        ).fetchone()
        if bot_status is None:
            status_value = str(existing["bot_status"]) if existing is not None else "unknown"
        else:
            status_value = (bot_status or "").strip().lower() or "unknown"
        if bot_can_manage is None:
            can_manage_value = int(existing["bot_can_manage"]) if existing is not None else 0
        else:
            can_manage_value = int(bool(bot_can_manage))
        if isinstance(verified_at, int):
            verified_at_value = max(0, verified_at)
        elif existing is not None:
            verified_at_value = int(existing["verified_at"] or 0)
        else:
            verified_at_value = 0
        if verified_by is None:
            verified_by_value = str(existing["verified_by"]) if existing is not None else ""
        else:
            verified_by_value = str(verified_by).strip()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO managed_chats(
                  chat_id, title, username, chat_type, source,
                  is_active, bot_status, bot_can_manage,
                  last_seen_at, updated_at, verified_at, verified_by
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                  title=excluded.title,
                  username=excluded.username,
                  chat_type=excluded.chat_type,
                  source=excluded.source,
                  is_active=excluded.is_active,
                  bot_status=excluded.bot_status,
                  bot_can_manage=excluded.bot_can_manage,
                  last_seen_at=excluded.last_seen_at,
                  updated_at=excluded.updated_at,
                  verified_at=excluded.verified_at,
                  verified_by=excluded.verified_by
                """,
                (
                    int(chat_id),
                    title.strip(),
                    username.strip().lstrip("@"),
                    chat_type.strip().lower(),
                    source.strip(),
                    int(bool(is_active)),
                    status_value,
                    can_manage_value,
                    now,
                    now,
                    verified_at_value,
                    verified_by_value,
                ),
            )

    def list_managed_chats(
        self,
        *,
        active_only: bool = True,
        manageable_only: bool = False,
        limit: int = 500,
    ) -> list[dict[str, int | str | bool]]:
        safe_limit = max(1, min(int(limit), 5000))
        where = []
        params: list[int] = []
        if active_only:
            where.append("is_active = 1")
        if manageable_only:
            where.append("bot_can_manage = 1")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            f"""
            SELECT
              chat_id, title, username, chat_type, source,
              is_active, bot_status, bot_can_manage,
              last_seen_at, updated_at, verified_at, verified_by
            FROM managed_chats
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
        result: list[dict[str, int | str | bool]] = []
        for row in rows:
            result.append(
                {
                    "chat_id": int(row["chat_id"]),
                    "title": str(row["title"] or ""),
                    "username": str(row["username"] or ""),
                    "chat_type": str(row["chat_type"] or ""),
                    "source": str(row["source"] or ""),
                    "is_active": bool(row["is_active"]),
                    "bot_status": str(row["bot_status"] or "unknown"),
                    "bot_can_manage": int(row["bot_can_manage"]) == 1,
                    "last_seen_at": int(row["last_seen_at"]),
                    "updated_at": int(row["updated_at"]),
                    "verified_at": int(row["verified_at"] or 0),
                    "verified_by": str(row["verified_by"] or ""),
                }
            )
        return result

    def list_known_chat_ids(self, *, limit: int = 1000) -> list[int]:
        safe_limit = max(1, min(int(limit), 10000))
        rows = self._conn.execute(
            """
            WITH known AS (
              SELECT chat_id FROM media_messages
              UNION
              SELECT chat_id FROM media_canonical
              UNION
              SELECT chat_id FROM pending_deletions
              UNION
              SELECT chat_id FROM tag_library
              UNION
              SELECT chat_id FROM tag_aliases
              UNION
              SELECT chat_id FROM tag_build_sent
            )
            SELECT chat_id
            FROM known
            ORDER BY ABS(chat_id) DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return [int(row["chat_id"]) for row in rows]

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

    def record_deletion_event(
        self,
        *,
        chat_id: int,
        message_id: int,
        event_type: str,
        reason: str,
        result: str,
        detail: str | None = None,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO deletion_events(
                  chat_id, message_id, event_type, reason, result, detail, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(chat_id),
                    int(message_id),
                    str(event_type).strip(),
                    str(reason).strip(),
                    str(result).strip(),
                    None if detail is None else str(detail),
                    now,
                ),
            )

    def list_deletion_events(
        self,
        *,
        chat_id: int | None = None,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict[str, int | str | None]]:
        safe_limit = max(1, min(int(limit), 1000))
        where: list[str] = []
        params: list[object] = []
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(int(chat_id))
        if event_type:
            where.append("event_type=?")
            params.append(str(event_type).strip())
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            f"""
            SELECT id, chat_id, message_id, event_type, reason, result, detail, created_at
            FROM deletion_events
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
        result: list[dict[str, int | str | None]] = []
        for row in rows:
            result.append(
                {
                    "id": int(row["id"]),
                    "chat_id": int(row["chat_id"]),
                    "message_id": int(row["message_id"]),
                    "event_type": str(row["event_type"] or ""),
                    "reason": str(row["reason"] or ""),
                    "result": str(row["result"] or ""),
                    "detail": None if row["detail"] is None else str(row["detail"]),
                    "created_at": int(row["created_at"]),
                }
            )
        return result

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

    def create_job(
        self,
        *,
        job_id: str,
        chat_id: int,
        task_type: str,
        payload_json: str,
        priority: int,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs(
                  job_id, chat_id, task_type, payload_json,
                  status, priority, created_at, started_at, finished_at, last_error,
                  scanned, matched, acted, failed,
                  attempt_count, max_attempts, next_run_at, worker_id, lease_expires_at,
                  last_heartbeat_at, terminal_reason, retryable_class, submitted_by,
                  session_snapshot_json, target_snapshot_json, policy_snapshot_json
                )
                VALUES(
                  ?, ?, ?, ?, 'pending', ?, ?, 0, 0, NULL,
                  0, 0, 0, 0,
                  0, 3, ?, '', 0,
                  0, '', '', '',
                  '{}', '{}', '{}'
                )
                ON CONFLICT(job_id) DO NOTHING
                """,
                (job_id, int(chat_id), task_type, payload_json, int(priority), now, now),
            )

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()

    def append_job_event(self, *, job_id: str, event_type: str, payload_json: str = '{}') -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO job_events(job_id, event_type, event_payload_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (job_id, event_type, payload_json, now),
            )

    def list_job_events(self, job_id: str, *, limit: int = 200) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 2000))
        rows = self._conn.execute(
            """
            SELECT *
            FROM job_events
            WHERE job_id=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (job_id, safe_limit),
        ).fetchall()
        return list(rows)

    def set_job_retry_wait(
        self,
        *,
        job_id: str,
        attempt_count: int,
        next_run_at: int,
        retryable_class: str,
        error: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE jobs
                SET status='retry_wait',
                    attempt_count=?,
                    next_run_at=?,
                    retryable_class=?,
                    last_error=?,
                    finished_at=0
                WHERE job_id=?
                """,
                (int(attempt_count), int(next_run_at), str(retryable_class), str(error), job_id),
            )

    def mark_job_dead_letter(
        self,
        *,
        job_id: str,
        attempt_count: int,
        retryable_class: str,
        terminal_reason: str,
        error: str,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                UPDATE jobs
                SET status='dead_letter',
                    attempt_count=?,
                    retryable_class=?,
                    terminal_reason=?,
                    last_error=?,
                    finished_at=?
                WHERE job_id=?
                """,
                (int(attempt_count), str(retryable_class), str(terminal_reason), str(error), now, job_id),
            )

    def list_dead_letter_jobs(self, *, limit: int = 200) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 1000))
        rows = self._conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE status='dead_letter'
            ORDER BY finished_at DESC, created_at DESC, job_id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return list(rows)

    def record_dead_letter_action(self, *, job_id: str, action: str, actor: str = '', note: str = '') -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO dead_letter_actions(job_id, action, actor, note, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (job_id, action, actor, note, now),
            )

    def acquire_target_lock(
        self,
        *,
        lock_key: str,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        now = int(time.time())
        expires = now + max(1, int(lease_seconds))
        row = self._conn.execute(
            "SELECT lock_key, job_id, lease_expires_at FROM target_locks WHERE lock_key=?",
            (lock_key,),
        ).fetchone()
        if row is not None:
            held_job = str(row['job_id'])
            held_until = int(row['lease_expires_at'] or 0)
            if held_job != job_id and held_until > now:
                return False
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO target_locks(lock_key, job_id, worker_id, lease_expires_at, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(lock_key) DO UPDATE SET
                    job_id=excluded.job_id,
                    worker_id=excluded.worker_id,
                    lease_expires_at=excluded.lease_expires_at,
                    updated_at=excluded.updated_at
                """,
                (lock_key, job_id, worker_id, expires, now, now),
            )
        return True

    def release_target_lock(self, *, lock_key: str, job_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM target_locks WHERE lock_key=? AND job_id=?",
                (lock_key, job_id),
            )

    def list_target_locks(self) -> list[sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM target_locks ORDER BY updated_at DESC, lock_key ASC"
        ).fetchall()
        return list(rows)

    def list_jobs(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        task_type: str | None = None,
        chat_id: int | None = None,
    ) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if task_type:
            clauses.append("task_type = ?")
            params.append(str(task_type))
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(int(chat_id))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM jobs
            {where_sql}
            ORDER BY created_at DESC, job_id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
        return list(rows)

    def update_job_status(self, job_id: str, status: str, error: str | None = None) -> None:
        now = int(time.time())
        finished_statuses = {"completed", "failed", "cancelled", "dead_letter", "failed_permanent"}
        with self._conn:
            if status == "running":
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status=?, started_at=CASE WHEN started_at=0 THEN ? ELSE started_at END, last_error=?
                    WHERE job_id=?
                    """,
                    (status, now, error, job_id),
                )
            elif status in finished_statuses:
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status=?, finished_at=?, last_error=?
                    WHERE job_id=?
                    """,
                    (status, now, error, job_id),
                )
            else:
                self._conn.execute(
                    "UPDATE jobs SET status=?, last_error=? WHERE job_id=?",
                    (status, error, job_id),
                )

    def upsert_job_checkpoint(self, *, job_id: str, stage: str, cursor_json: str) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO job_checkpoints(job_id, stage, cursor_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(job_id, stage) DO UPDATE SET
                  cursor_json=excluded.cursor_json,
                  updated_at=excluded.updated_at
                """,
                (job_id, stage, cursor_json, now),
            )

    def get_job_checkpoint(self, job_id: str, stage: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM job_checkpoints WHERE job_id=? AND stage=?",
            (job_id, stage),
        ).fetchone()

    def record_job_action(
        self,
        *,
        idempotency_key: str,
        job_id: str,
        chat_id: int,
        message_id: int,
        action: str,
        status: str,
        error: str | None,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO job_actions(
                  idempotency_key, job_id, chat_id, message_id, action,
                  status, attempts, error, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                  status=excluded.status,
                  error=excluded.error,
                  attempts=job_actions.attempts + 1,
                  updated_at=excluded.updated_at
                """,
                (idempotency_key, job_id, int(chat_id), int(message_id), action, status, error, now),
            )

    def update_job_progress(
        self,
        job_id: str,
        *,
        scanned: int,
        matched: int,
        acted: int,
        failed: int,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE jobs
                SET scanned=?, matched=?, acted=?, failed=?
                WHERE job_id=?
                """,
                (int(scanned), int(matched), int(acted), int(failed), job_id),
            )

    def get_job_action(self, idempotency_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM job_actions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()

    def list_telegram_controllers(self, *, enabled_only: bool = False) -> list[dict[str, int | str | bool]]:
        where_sql = "WHERE enabled = 1" if enabled_only else ""
        rows = self._conn.execute(
            f"""
            SELECT user_id, display_name, role, enabled, is_primary, source, created_at, updated_at, last_verified_at
            FROM telegram_controllers
            {where_sql}
            ORDER BY is_primary DESC, enabled DESC, updated_at DESC, user_id ASC
            """
        ).fetchall()
        result: list[dict[str, int | str | bool]] = []
        for row in rows:
            result.append(
                {
                    "user_id": int(row["user_id"]),
                    "display_name": str(row["display_name"] or ""),
                    "role": str(row["role"] or "operator"),
                    "enabled": int(row["enabled"]) == 1,
                    "is_primary": int(row["is_primary"]) == 1,
                    "source": str(row["source"] or ""),
                    "created_at": int(row["created_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                    "last_verified_at": int(row["last_verified_at"] or 0),
                }
            )
        return result

    def upsert_telegram_controller(
        self,
        *,
        user_id: int,
        display_name: str,
        enabled: bool,
        is_primary: bool,
        source: str,
        role: str = "operator",
    ) -> None:
        now = int(time.time())
        normalized_role = str(role or "operator").strip().lower() or "operator"
        if normalized_role not in {"owner", "admin", "operator", "readonly"}:
            normalized_role = "operator"
        with self._conn:
            if is_primary:
                self._conn.execute("UPDATE telegram_controllers SET is_primary=0")
            self._conn.execute(
                """
                INSERT INTO telegram_controllers(
                  user_id, display_name, role, enabled, is_primary, source, created_at, updated_at, last_verified_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  display_name=excluded.display_name,
                  role=excluded.role,
                  enabled=excluded.enabled,
                  is_primary=excluded.is_primary,
                  source=excluded.source,
                  updated_at=excluded.updated_at,
                  last_verified_at=excluded.last_verified_at
                """,
                (
                    int(user_id),
                    str(display_name or "").strip(),
                    normalized_role,
                    int(bool(enabled)),
                    int(bool(is_primary)),
                    str(source or "").strip(),
                    now,
                    now,
                    now,
                ),
            )
            self._ensure_primary_telegram_controller_locked()

    def set_primary_telegram_controller(self, *, user_id: int) -> None:
        now = int(time.time())
        with self._conn:
            row = self._conn.execute(
                "SELECT user_id FROM telegram_controllers WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                raise ValueError("controller_user_not_found")
            self._conn.execute("UPDATE telegram_controllers SET is_primary=0")
            self._conn.execute(
                """
                UPDATE telegram_controllers
                SET is_primary=1, enabled=1, updated_at=?
                WHERE user_id=?
                """,
                (now, int(user_id)),
            )

    def set_telegram_controller_enabled(self, *, user_id: int, enabled: bool) -> None:
        now = int(time.time())
        with self._conn:
            row = self._conn.execute(
                "SELECT user_id, is_primary, enabled FROM telegram_controllers WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                raise ValueError("controller_user_not_found")
            target_enabled = int(bool(enabled))
            if int(row["enabled"]) == target_enabled:
                return
            if target_enabled == 0 and int(row["is_primary"]) == 1:
                enabled_count = int(
                    self._conn.execute(
                        "SELECT COUNT(*) AS c FROM telegram_controllers WHERE enabled=1"
                    ).fetchone()["c"]
                )
                if enabled_count <= 1:
                    raise ValueError("cannot_disable_only_enabled_primary")
            self._conn.execute(
                "UPDATE telegram_controllers SET enabled=?, updated_at=? WHERE user_id=?",
                (target_enabled, now, int(user_id)),
            )
            self._ensure_primary_telegram_controller_locked()

    def delete_telegram_controller(self, *, user_id: int) -> None:
        with self._conn:
            row = self._conn.execute(
                "SELECT user_id, is_primary, enabled FROM telegram_controllers WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                raise ValueError("controller_user_not_found")
            if int(row["is_primary"]) == 1 and int(row["enabled"]) == 1:
                enabled_count = int(
                    self._conn.execute(
                        "SELECT COUNT(*) AS c FROM telegram_controllers WHERE enabled=1"
                    ).fetchone()["c"]
                )
                if enabled_count <= 1:
                    raise ValueError("cannot_delete_only_enabled_primary")
            self._conn.execute(
                "DELETE FROM telegram_controllers WHERE user_id=?",
                (int(user_id),),
            )
            self._ensure_primary_telegram_controller_locked()

    def _ensure_primary_telegram_controller_locked(self) -> None:
        enabled_rows = self._conn.execute(
            "SELECT user_id, is_primary FROM telegram_controllers WHERE enabled=1 ORDER BY updated_at DESC, user_id ASC"
        ).fetchall()
        if not enabled_rows:
            return
        current_primary = next((row for row in enabled_rows if int(row["is_primary"]) == 1), None)
        if current_primary is not None:
            return
        chosen = int(enabled_rows[0]["user_id"])
        self._conn.execute("UPDATE telegram_controllers SET is_primary=0")
        self._conn.execute(
            "UPDATE telegram_controllers SET is_primary=1 WHERE user_id=?",
            (chosen,),
        )

    def list_providers(self, *, enabled_only: bool = False) -> list[dict[str, str | bool | int]]:
        where_sql = "WHERE enabled = 1" if enabled_only else ""
        rows = self._conn.execute(
            f"""
            SELECT provider_key, display_name, provider_type, base_url, enabled, use_responses_mode, default_model,
                   last_test_status, last_test_at, last_probe_status, last_probe_at, supports_responses,
                   capabilities_json, created_at, updated_at
            FROM providers
            {where_sql}
            ORDER BY enabled DESC, updated_at DESC, provider_key ASC
            """
        ).fetchall()
        result: list[dict[str, str | bool | int]] = []
        for row in rows:
            result.append(
                {
                    "provider_key": str(row["provider_key"]),
                    "display_name": str(row["display_name"] or ""),
                    "provider_type": str(row["provider_type"] or ""),
                    "base_url": str(row["base_url"] or ""),
                    "enabled": int(row["enabled"]) == 1,
                    "use_responses_mode": str(row["use_responses_mode"] or "auto"),
                    "default_model": str(row["default_model"] or ""),
                    "last_test_status": str(row["last_test_status"] or ""),
                    "last_test_at": int(row["last_test_at"] or 0),
                    "last_probe_status": str(row["last_probe_status"] or ""),
                    "last_probe_at": int(row["last_probe_at"] or 0),
                    "supports_responses": int(row["supports_responses"] or 0) == 1,
                    "capabilities_json": str(row["capabilities_json"] or "{}"),
                    "created_at": int(row["created_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                }
            )
        return result

    def get_provider(self, *, provider_key: str) -> dict[str, str | bool | int] | None:
        row = self._conn.execute(
            """
            SELECT provider_key, display_name, provider_type, base_url, enabled, use_responses_mode, default_model,
                   last_test_status, last_test_at, last_probe_status, last_probe_at, supports_responses,
                   capabilities_json, created_at, updated_at
            FROM providers
            WHERE provider_key=?
            """,
            (str(provider_key).strip(),),
        ).fetchone()
        if row is None:
            return None
        return {
            "provider_key": str(row["provider_key"]),
            "display_name": str(row["display_name"] or ""),
            "provider_type": str(row["provider_type"] or ""),
            "base_url": str(row["base_url"] or ""),
            "enabled": int(row["enabled"]) == 1,
            "use_responses_mode": str(row["use_responses_mode"] or "auto"),
            "default_model": str(row["default_model"] or ""),
            "last_test_status": str(row["last_test_status"] or ""),
            "last_test_at": int(row["last_test_at"] or 0),
            "last_probe_status": str(row["last_probe_status"] or ""),
            "last_probe_at": int(row["last_probe_at"] or 0),
            "supports_responses": int(row["supports_responses"] or 0) == 1,
            "capabilities_json": str(row["capabilities_json"] or "{}"),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def upsert_provider(
        self,
        *,
        provider_key: str,
        display_name: str,
        provider_type: str,
        base_url: str,
        enabled: bool,
        use_responses_mode: str,
        default_model: str,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO providers(
                  provider_key, display_name, provider_type, base_url, enabled,
                  use_responses_mode, default_model, last_test_status, last_test_at,
                  last_probe_status, last_probe_at, supports_responses, capabilities_json,
                  created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, '', 0, '', 0, 0, '{}', ?, ?)
                ON CONFLICT(provider_key) DO UPDATE SET
                  display_name=excluded.display_name,
                  provider_type=excluded.provider_type,
                  base_url=excluded.base_url,
                  enabled=excluded.enabled,
                  use_responses_mode=excluded.use_responses_mode,
                  default_model=excluded.default_model,
                  updated_at=excluded.updated_at
                """,
                (
                    str(provider_key).strip(),
                    str(display_name or "").strip(),
                    str(provider_type or "").strip(),
                    str(base_url or "").strip(),
                    int(bool(enabled)),
                    str(use_responses_mode or "auto").strip(),
                    str(default_model or "").strip(),
                    now,
                    now,
                ),
            )

    def mark_provider_test_result(self, *, provider_key: str, status: str) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                UPDATE providers
                SET last_test_status=?, last_test_at=?, updated_at=?
                WHERE provider_key=?
                """,
                (str(status or "").strip(), now, now, str(provider_key).strip()),
            )

    def mark_provider_probe_result(
        self,
        *,
        provider_key: str,
        status: str,
        supports_responses: bool,
        capabilities_json: str,
    ) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                UPDATE providers
                SET last_probe_status=?, last_probe_at=?, supports_responses=?, capabilities_json=?, updated_at=?
                WHERE provider_key=?
                """,
                (
                    str(status or "").strip(),
                    now,
                    int(bool(supports_responses)),
                    str(capabilities_json or "{}"),
                    now,
                    str(provider_key).strip(),
                ),
            )

    def list_models(
        self,
        *,
        provider_key: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, str | bool | int]]:
        clauses: list[str] = []
        params: list[object] = []
        if provider_key:
            clauses.append("provider_key=?")
            params.append(str(provider_key).strip())
        if enabled_only:
            clauses.append("enabled=1")
        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"""
            SELECT provider_key, model_id, enabled, source, created_at, updated_at
            FROM models
            {where_sql}
            ORDER BY enabled DESC, updated_at DESC, provider_key ASC, model_id ASC
            """,
            tuple(params),
        ).fetchall()
        result: list[dict[str, str | bool | int]] = []
        for row in rows:
            result.append(
                {
                    "provider_key": str(row["provider_key"]),
                    "model_id": str(row["model_id"]),
                    "enabled": int(row["enabled"]) == 1,
                    "source": str(row["source"] or "sync"),
                    "created_at": int(row["created_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                }
            )
        return result

    def upsert_model(self, *, provider_key: str, model_id: str, enabled: bool, source: str = "sync") -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO models(provider_key, model_id, enabled, source, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_key, model_id) DO UPDATE SET
                  enabled=excluded.enabled,
                  source=excluded.source,
                  updated_at=excluded.updated_at
                """,
                (
                    str(provider_key).strip(),
                    str(model_id).strip(),
                    int(bool(enabled)),
                    str(source or "sync").strip(),
                    now,
                    now,
                ),
            )

    def record_model_sync_run(self, *, trigger_source: str, synced_count: int) -> None:
        now = int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO model_sync_runs(trigger_source, synced_count, created_at)
                VALUES(?, ?, ?)
                """,
                (str(trigger_source or "manual"), int(synced_count), now),
            )

    def record_ai_request_event(
        self,
        *,
        provider_key: str,
        model_key: str,
        success: bool,
        fallback_used: bool,
        downgrade_used: bool,
        latency_ms: int,
        created_at: int | None = None,
    ) -> None:
        now = int(created_at) if created_at is not None else int(time.time())
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO ai_request_events(
                  provider_key, model_key, success, fallback_used, downgrade_used, latency_ms, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(provider_key or "").strip(),
                    str(model_key or "").strip(),
                    int(bool(success)),
                    int(bool(fallback_used)),
                    int(bool(downgrade_used)),
                    int(latency_ms),
                    now,
                ),
            )

    def get_ai_request_metrics_by_provider(self, *, since_ts: int) -> dict[str, dict[str, float | int]]:
        rows = self._conn.execute(
            """
            SELECT provider_key,
                   COUNT(*) AS total,
                   SUM(success) AS success_count,
                   AVG(latency_ms) AS avg_latency_ms,
                   SUM(fallback_used) AS fallback_count,
                   SUM(downgrade_used) AS downgrade_count
            FROM ai_request_events
            WHERE created_at >= ?
            GROUP BY provider_key
            """,
            (int(since_ts),),
        ).fetchall()

        latency_rows = self._conn.execute(
            """
            SELECT provider_key, latency_ms
            FROM ai_request_events
            WHERE created_at >= ?
            ORDER BY provider_key ASC, latency_ms ASC
            """,
            (int(since_ts),),
        ).fetchall()
        latency_map: dict[str, list[int]] = {}
        for row in latency_rows:
            key = str(row["provider_key"])
            latency_map.setdefault(key, []).append(int(row["latency_ms"] or 0))

        def p95(values: list[int]) -> int:
            if not values:
                return 0
            n = len(values)
            rank = (95 * n + 99) // 100
            idx = min(max(rank - 1, 0), n - 1)
            return int(values[idx])

        result: dict[str, dict[str, float | int]] = {}
        for row in rows:
            provider_key = str(row["provider_key"])
            total = int(row["total"] or 0)
            success_count = int(row["success_count"] or 0)
            success_rate = float(success_count / total) if total > 0 else 0.0
            latencies = latency_map.get(provider_key, [])
            result[provider_key] = {
                "request_count": total,
                "success_rate": success_rate,
                "avg_latency_ms": int(float(row["avg_latency_ms"] or 0.0)),
                "p95_latency_ms": p95(latencies),
                "fallback_count": int(row["fallback_count"] or 0),
                "downgrade_count": int(row["downgrade_count"] or 0),
            }
        return result

    def get_ai_request_metrics_by_model(self, *, since_ts: int) -> dict[str, dict[str, float | int]]:
        rows = self._conn.execute(
            """
            SELECT model_key,
                   COUNT(*) AS total,
                   SUM(success) AS success_count
            FROM ai_request_events
            WHERE created_at >= ?
            GROUP BY model_key
            """,
            (int(since_ts),),
        ).fetchall()
        result: dict[str, dict[str, float | int]] = {}
        for row in rows:
            total = int(row["total"] or 0)
            success_count = int(row["success_count"] or 0)
            success_rate = float(success_count / total) if total > 0 else 0.0
            result[str(row["model_key"])] = {
                "request_count": total,
                "success_rate": success_rate,
            }
        return result

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
