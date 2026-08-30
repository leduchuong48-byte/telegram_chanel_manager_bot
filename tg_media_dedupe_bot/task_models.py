from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobType(str, Enum):
    SCAN = "scan"
    SCAN_DELETE = "scan_delete"
    DELETE_BY_TYPE = "delete_by_type"
    BATCH_DELETE = "batch_delete"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"
    DEAD_LETTER = "dead_letter"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobProgress:
    scanned: int = 0
    matched: int = 0
    acted: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "matched": self.matched,
            "acted": self.acted,
            "failed": self.failed,
        }


@dataclass(slots=True)
class JobSpec:
    job_id: str
    chat_id: int
    job_type: JobType
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    priority: int = 0
    progress: JobProgress = field(default_factory=JobProgress)
