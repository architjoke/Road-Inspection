"""Phase 6 task submission and status API helpers."""

from __future__ import annotations

import logging

from flask import current_app

from app.workers import executor as analysis_executor
from app.workers.inspection_worker import analyze_inspection_job
from app.workers.task_store import (
    TASK_TYPE_INSPECTION_ANALYSIS,
    task_store,
)

logger = logging.getLogger(__name__)


def submit_inspection_analysis(
    inspection_id: int,
    media_id: int | None = None,
    *,
    allow_duplicate: bool = False,
) -> dict:
    """
    Enqueue background analysis and return task metadata immediately.

    If an active (PENDING/RUNNING) task already exists for the same
    inspection/media, reuse it unless allow_duplicate=True.
    """
    if not allow_duplicate:
        existing = task_store.find_active(inspection_id, media_id)
        if existing is not None:
            logger.info(
                "Analysis REUSED active task=%s inspection=%s media=%s",
                existing.task_id,
                inspection_id,
                media_id,
            )
            payload = existing.to_dict()
            payload["reused"] = True
            return payload

    task = task_store.create(
        task_type=TASK_TYPE_INSPECTION_ANALYSIS,
        inspection_id=inspection_id,
        media_id=media_id,
    )

    app = current_app._get_current_object()
    analysis_executor.submit(
        analyze_inspection_job,
        task.task_id,
        inspection_id,
        media_id,
        app,
    )

    logger.info(
        "Analysis SUBMITTED task=%s inspection=%s media=%s",
        task.task_id,
        inspection_id,
        media_id,
    )
    payload = task.to_dict()
    payload["reused"] = False
    return payload


def get_task_status(task_id: str) -> dict | None:
    task = task_store.get(task_id)
    return task.to_dict() if task else None


def get_task_result(task_id: str) -> dict | None:
    task = task_store.get(task_id)
    if task is None:
        return None
    return task.to_dict()
