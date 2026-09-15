"""Manual detection review actions (offline-safe memory store)."""

from __future__ import annotations

from typing import Any

from app.services.detection_service import (
    list_memory_detections,
    update_memory_detection,
)


ALLOWED_DECISIONS = {"approved", "rejected", "needs_field_check"}


def review_detection(
    detection_id: int,
    *,
    decision: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
) -> dict[str, Any] | None:
    decision = (decision or "").strip().lower()
    if decision not in ALLOWED_DECISIONS:
        raise ValueError("Decision must be approved, rejected, or needs_field_check.")

    status_map = {
        "approved": "APPROVED",
        "rejected": "REJECTED",
        "needs_field_check": "NEEDS_FIELD_CHECK",
    }
    return update_memory_detection(
        detection_id,
        review_decision=decision,
        review_comment=comment,
        reviewer_name=reviewer_name,
        status=status_map[decision],
        manual_review_required=decision == "needs_field_check",
    )


def list_reviewed() -> list[dict[str, Any]]:
    return [row for row in list_memory_detections() if row.get("review_decision")]
