"""In-memory analysis task store (offline-safe; no RDS required)."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any


TASK_PENDING = "PENDING"
TASK_RUNNING = "RUNNING"
TASK_SUCCEEDED = "SUCCEEDED"
TASK_FAILED = "FAILED"

TASK_TYPE_INSPECTION_ANALYSIS = "inspection_analysis"
ACTIVE_STATUSES = {TASK_PENDING, TASK_RUNNING}


@dataclass
class AnalysisTask:
    task_id: str
    task_type: str
    inspection_id: int | None
    media_id: int | None
    status: str = TASK_PENDING
    progress: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    result_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("created_at", "started_at", "completed_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.isoformat() + "Z"
        return payload


class TaskStore:
    """Thread-safe in-process task repository with retention pruning."""

    def __init__(self, max_tasks: int = 500, retain_hours: int = 24) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, AnalysisTask] = {}
        self._max_tasks = max(50, max_tasks)
        self._retain_hours = max(1, retain_hours)

    def create(
        self,
        *,
        task_type: str = TASK_TYPE_INSPECTION_ANALYSIS,
        inspection_id: int | None = None,
        media_id: int | None = None,
    ) -> AnalysisTask:
        task = AnalysisTask(
            task_id=str(uuid.uuid4()),
            task_type=task_type,
            inspection_id=inspection_id,
            media_id=media_id,
            status=TASK_PENDING,
        )
        with self._lock:
            self._prune_locked()
            self._tasks[task.task_id] = task
        return deepcopy(task)

    def find_active(
        self,
        inspection_id: int,
        media_id: int | None = None,
    ) -> AnalysisTask | None:
        """Return an existing PENDING/RUNNING task for the same inspection/media."""
        with self._lock:
            for task in self._tasks.values():
                if task.inspection_id != inspection_id:
                    continue
                if task.status not in ACTIVE_STATUSES:
                    continue
                if media_id is not None and task.media_id != media_id:
                    continue
                return deepcopy(task)
        return None

    def get(self, task_id: str) -> AnalysisTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task else None

    def update(self, task_id: str, **fields: Any) -> AnalysisTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            return deepcopy(task)

    def list_for_inspection(self, inspection_id: int) -> list[AnalysisTask]:
        with self._lock:
            return [
                deepcopy(task)
                for task in self._tasks.values()
                if task.inspection_id == inspection_id
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()

    def _prune_locked(self) -> None:
        """Drop old terminal tasks when over capacity (caller holds lock)."""
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=self._retain_hours)
        terminal = {
            tid: task
            for tid, task in self._tasks.items()
            if task.status not in ACTIVE_STATUSES
            and (task.completed_at or task.created_at) < cutoff
        }
        for tid in terminal:
            del self._tasks[tid]

        if len(self._tasks) <= self._max_tasks:
            return

        # Evict oldest completed/failed first
        candidates = sorted(
            (
                (tid, task)
                for tid, task in self._tasks.items()
                if task.status not in ACTIVE_STATUSES
            ),
            key=lambda item: item[1].completed_at or item[1].created_at,
        )
        overflow = len(self._tasks) - self._max_tasks
        for tid, _ in candidates[:overflow]:
            del self._tasks[tid]


# Process-wide singleton used by the Flask app and workers.
task_store = TaskStore()
