"""Manual detection review actions.

Every decision updates the in-memory cache *and*, when the database is
available, the ``detections`` row (status / verified / false positive) plus an
audit row in ``detection_reviews`` — so reviews survive restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.services.detection_service import update_memory_detection

logger = logging.getLogger(__name__)

ALLOWED_DECISIONS = {"approved", "rejected", "needs_field_check"}

STATUS_MAP = {
    "approved": "APPROVED",
    "rejected": "REJECTED",
    "needs_field_check": "NEEDS_FIELD_CHECK",
}
# detection_reviews.review_type CHECK values
REVIEW_TYPE = {
    "approved": "verification",
    "rejected": "false_positive",
    "needs_field_check": "reinspection",
}


def _db_enabled() -> bool:
    try:
        from flask import current_app

        from app.services.offline_store import offline_mode_enabled

        return not offline_mode_enabled() and not current_app.config.get("TESTING")
    except Exception:
        return False


def _persist_review(
    detection_id: int,
    decision: str,
    reviewer_id: int | None,
    comment: str | None,
) -> bool:
    """Write the decision to Supabase. Returns True when the detection row exists."""
    from sqlalchemy import text

    from app.extensions import db

    now = datetime.utcnow()
    try:
        row = db.session.execute(
            text("SELECT severity_level, priority_level FROM detections WHERE detection_id = :id"),
            {"id": detection_id},
        ).first()
        if row is None:
            return False
        if reviewer_id is not None and db.session.execute(
            text("SELECT 1 FROM users WHERE id = :id"), {"id": reviewer_id}
        ).first() is None:
            reviewer_id = None  # e.g. platform super admins live in super_admins
        db.session.execute(
            text(
                "UPDATE detections SET status = :status, is_verified = :verified, "
                "is_false_positive = :fp, verified_by = :by, verified_at = :at, "
                "verification_notes = :notes, updated_at = :at WHERE detection_id = :id"
            ),
            {
                "status": STATUS_MAP[decision],
                "verified": decision == "approved",
                "fp": decision == "rejected",
                "by": reviewer_id,
                "at": now,
                "notes": comment,
                "id": detection_id,
            },
        )
        if reviewer_id is not None:  # reviewer_id is NOT NULL + FK users(id)
            db.session.execute(
                text(
                    "INSERT INTO detection_reviews (detection_id, reviewer_id, review_type, "
                    "original_severity, original_priority, is_approved, is_false_positive, "
                    "requires_reinspection, review_notes, reviewed_at, created_at) VALUES "
                    "(:id, :by, :type, :sev, :pri, :ok, :fp, :re, :notes, :at, :at)"
                ),
                {
                    "id": detection_id,
                    "by": reviewer_id,
                    "type": REVIEW_TYPE[decision],
                    "sev": row[0],
                    "pri": row[1],
                    "ok": decision == "approved",
                    "fp": decision == "rejected",
                    "re": decision == "needs_field_check",
                    "notes": comment,
                    "at": now,
                },
            )
        db.session.commit()
        return True
    except Exception:
        logger.exception("Persisting review for detection %s failed", detection_id)
        db.session.rollback()
        return False


def review_detection(
    detection_id: int,
    *,
    decision: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
    reviewer_id: int | None = None,
) -> dict[str, Any] | None:
    decision = (decision or "").strip().lower()
    if decision not in ALLOWED_DECISIONS:
        raise ValueError("Decision must be approved, rejected, or needs_field_check.")

    in_db = _persist_review(detection_id, decision, reviewer_id, comment) if _db_enabled() else False
    updated = update_memory_detection(
        detection_id,
        review_decision=decision,
        review_comment=comment,
        reviewer_name=reviewer_name,
        status=STATUS_MAP[decision],
        manual_review_required=decision == "needs_field_check",
    )
    if updated is None and in_db:
        # Not cached in this process (e.g. after a restart): return the DB view.
        from app.services.detection_review_service import get_detection_for_review

        updated = get_detection_for_review(detection_id)
    return updated
