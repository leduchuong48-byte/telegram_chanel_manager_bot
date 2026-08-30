from __future__ import annotations

import os
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tg_media_dedupe_bot.config import Config, load_config
from tg_media_dedupe_bot.paths import resolve_app_path, telethon_session_db_path
from tg_media_dedupe_bot.reliability import classify_delete_error


@dataclass
class ComponentStatus:
    status: str
    detail: str = "ok"
    path: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"status": self.status, "detail": self.detail}
        if self.path:
            result["path"] = self.path
        return result


@dataclass
class BotRuntimeState:
    status: str = "starting"
    detail: str = "bot task has not reported yet"
    updated_at: float = field(default_factory=time.time)
    last_ok_at: float = 0.0
    last_error_at: float = 0.0
    consecutive_failures: int = 0


_BOT_RUNTIME_STATE = BotRuntimeState()


def set_bot_runtime_state(status: str, detail: str = "ok") -> None:
    _BOT_RUNTIME_STATE.status = status
    _BOT_RUNTIME_STATE.detail = detail
    _BOT_RUNTIME_STATE.updated_at = time.time()
    if status in {"running", "ok"}:
        _BOT_RUNTIME_STATE.last_ok_at = _BOT_RUNTIME_STATE.updated_at
        _BOT_RUNTIME_STATE.consecutive_failures = 0
    elif status in {"degraded", "fail"}:
        _BOT_RUNTIME_STATE.last_error_at = _BOT_RUNTIME_STATE.updated_at
        _BOT_RUNTIME_STATE.consecutive_failures += 1


def get_bot_runtime_state() -> dict[str, Any]:
    return {
        "status": _BOT_RUNTIME_STATE.status,
        "detail": _BOT_RUNTIME_STATE.detail,
        "updated_at": int(_BOT_RUNTIME_STATE.updated_at),
        "last_ok_at": int(_BOT_RUNTIME_STATE.last_ok_at),
        "last_error_at": int(_BOT_RUNTIME_STATE.last_error_at),
        "consecutive_failures": int(_BOT_RUNTIME_STATE.consecutive_failures),
    }


def _is_writable_path(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.R_OK | os.W_OK)
    return os.access(path.parent, os.W_OK)


def check_config() -> ComponentStatus:
    path = resolve_app_path("config.json")
    if not path.exists():
        return ComponentStatus("fail", "config.json does not exist", str(path))
    if not os.access(path, os.R_OK):
        return ComponentStatus("fail", "config.json is not readable", str(path))
    return ComponentStatus("ok", "config readable", str(path))


def check_db(cfg: Config) -> ComponentStatus:
    path = Path(cfg.db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ComponentStatus("fail", f"DB parent cannot be created: {exc}", str(path))
    if not os.access(path.parent, os.W_OK):
        return ComponentStatus("fail", "DB parent is not writable", str(path.parent))
    try:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or str(result[0]).lower() != "ok":
                return ComponentStatus("fail", f"SQLite quick_check failed: {result}", str(path))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return ComponentStatus("fail", f"SQLite open/check failed: {exc}", str(path))
    return ComponentStatus("ok", "database open and quick_check ok", str(path))


def check_telethon_session(cfg: Config) -> ComponentStatus:
    path = telethon_session_db_path(cfg.tg_session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ComponentStatus("fail", f"TG_SESSION parent cannot be created: {exc}", str(path))
    if not os.access(path.parent, os.W_OK):
        return ComponentStatus("fail", f"TG_SESSION parent is not writable by uid={os.getuid()}", str(path.parent))
    if path.exists() and not _is_writable_path(path):
        mode = stat.S_IMODE(path.stat().st_mode)
        return ComponentStatus(
            "fail",
            f"TG_SESSION {path} is not readable/writable by uid={os.getuid()} mode={mode:o}",
            str(path),
        )
    try:
        conn = sqlite3.connect(path, timeout=30)
        conn.close()
    except sqlite3.Error as exc:
        return ComponentStatus("fail", f"TG_SESSION SQLite open failed: {exc}", str(path))
    return ComponentStatus("ok", "session file openable", str(path))


def check_queue_metrics(path: str | Path) -> dict[str, Any]:
    db_path = Path(path)
    metrics: dict[str, Any] = {
        "status": "ok",
        "detail": "queue metrics readable",
        "pending_deletions": 0,
        "pending_terminal_candidates": 0,
        "forwarding_sending": 0,
        "forwarding_failed": 0,
        "forwarding_delivery_unknown": 0,
    }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "pending_deletions" in tables:
                metrics["pending_deletions"] = int(
                    conn.execute("SELECT COUNT(*) FROM pending_deletions").fetchone()[0]
                )
            if {"pending_deletions", "deletion_attempts"}.issubset(tables):
                rows = conn.execute(
                    """
                    SELECT a.error
                    FROM pending_deletions AS p
                    JOIN deletion_attempts AS a
                      ON a.chat_id=p.chat_id AND a.message_id=p.message_id
                    WHERE a.result='failed'
                    """
                ).fetchall()
                metrics["pending_terminal_candidates"] = sum(
                    1
                    for row in rows
                    if classify_delete_error(RuntimeError(str(row["error"] or ""))).disposition == "terminal"
                )
            if "forwarded_messages" in tables:
                status_counts = {
                    str(row["status"]): int(row["n"])
                    for row in conn.execute(
                        "SELECT status, COUNT(*) AS n FROM forwarded_messages GROUP BY status"
                    ).fetchall()
                }
                metrics["forwarding_sending"] = status_counts.get("sending", 0)
                metrics["forwarding_failed"] = status_counts.get("failed", 0)
                metrics["forwarding_delivery_unknown"] = status_counts.get("delivery_unknown", 0)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        metrics["status"] = "fail"
        metrics["detail"] = f"queue metrics unavailable: {exc}"
    return metrics


def run_component_checks(*, include_bot: bool = False) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    try:
        cfg = load_config()
        components["config"] = check_config().as_dict()
        components["db"] = check_db(cfg).as_dict()
        components["telethon_session"] = check_telethon_session(cfg).as_dict()
        components["bot_token"] = ComponentStatus("ok" if cfg.bot_token else "fail", "configured" if cfg.bot_token else "missing").as_dict()
        components["queues"] = check_queue_metrics(cfg.db_path)
    except Exception as exc:  # noqa: BLE001
        components["config"] = ComponentStatus("fail", str(exc)).as_dict()

    if include_bot:
        components["bot_polling"] = get_bot_runtime_state()

    status_value = "ok"
    for component in components.values():
        if component.get("status") not in {"ok", "running"}:
            status_value = "fail"
            break
    return {"status": status_value, "components": components}


def preflight_or_raise() -> None:
    result = run_component_checks(include_bot=False)
    if result["status"] == "ok":
        return
    failures = []
    for name, component in result["components"].items():
        if component.get("status") in {"ok", "running"}:
            continue
        detail = component.get("detail", "")
        path = component.get("path", "")
        failures.append(f"{name}: {detail} {path}".strip())
    raise RuntimeError("startup preflight failed: " + "; ".join(failures))
