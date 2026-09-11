"""Offline Phase 6 task / executor tests."""

from __future__ import annotations

import time
from datetime import datetime

from app.workers import executor as analysis_executor
from app.workers.task_store import (
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    task_store,
)


def test_task_submission_returns_id():
    task = task_store.create(inspection_id=1, media_id=2)
    assert task.task_id
    assert task.status == TASK_PENDING
    assert task.inspection_id == 1
    assert task.media_id == 2
    assert isinstance(task.created_at, datetime)


def test_task_lifecycle_timestamps_and_error():
    task = task_store.create(inspection_id=9)
    task_store.update(
        task.task_id,
        status=TASK_RUNNING,
        started_at=datetime.utcnow(),
        progress=10,
    )
    running = task_store.get(task.task_id)
    assert running.status == TASK_RUNNING
    assert running.started_at is not None

    task_store.update(
        task.task_id,
        status=TASK_FAILED,
        completed_at=datetime.utcnow(),
        error_message="boom",
        progress=10,
    )
    failed = task_store.get(task.task_id)
    assert failed.status == TASK_FAILED
    assert failed.error_message == "boom"
    assert failed.completed_at is not None


def test_successful_task_reaches_succeeded():
    task = task_store.create(inspection_id=3)
    task_store.update(
        task.task_id,
        status=TASK_SUCCEEDED,
        completed_at=datetime.utcnow(),
        progress=100,
        result_summary={"detection_count": 2},
    )
    done = task_store.get(task.task_id)
    assert done.status == TASK_SUCCEEDED
    assert done.result_summary["detection_count"] == 2


def test_executor_is_bounded_and_reused():
    first = analysis_executor.get_executor()
    second = analysis_executor.get_executor()
    assert first is second
    assert analysis_executor.executor_max_workers() >= 1

    future = analysis_executor.submit(lambda: 42)
    assert future.result(timeout=5) == 42


def test_worker_exception_captured_via_task_update():
    """Simulate worker failure bookkeeping without RDS."""
    task = task_store.create(inspection_id=5)

    def _failing_job(task_id: str):
        task_store.update(task_id, status=TASK_RUNNING, started_at=datetime.utcnow())
        try:
            raise RuntimeError("simulated worker crash")
        except Exception as exc:
            task_store.update(
                task_id,
                status=TASK_FAILED,
                completed_at=datetime.utcnow(),
                error_message=str(exc),
            )

    future = analysis_executor.submit(_failing_job, task.task_id)
    future.result(timeout=5)
    # Allow thread bookkeeping
    time.sleep(0.05)
    failed = task_store.get(task.task_id)
    assert failed.status == TASK_FAILED
    assert "simulated worker crash" in (failed.error_message or "")
