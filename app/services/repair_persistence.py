"""Persist repair jobs in Supabase (``tasks`` table, ``task_type = 'repair'``).

The repair workflow keeps a fast in-memory working set (``repair_service``).
This module makes it durable and shared between server processes:

* every create / update is written through to ``tasks``
* the working set is reloaded from ``tasks`` at most every few seconds

Column mapping
--------------
task_id -> job id, status (mapped below), assigned_to/contractor_id -> assignee,
priority <- severity, title/description, location lat/lng/geometry,
started_at/completed_at. App-only fields with no column of their own (exact
workflow status, payment status, evidence file, verification result, ...) are
kept in the ``repair_materials`` JSONB column under ``"app_workflow"``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# app status -> tasks.status (CHECK: pending, assigned, in_progress, completed, cancelled, reopened)
STATUS_TO_DB = {
    "open": "pending",
    "assigned": "assigned",
    "in_progress": "in_progress",
    "evidence_uploaded": "in_progress",
    "verified": "completed",
    "closed": "completed",
    "paid": "completed",
    "rejected": "reopened",
}
DB_TO_STATUS = {"pending": "open", "assigned": "assigned", "in_progress": "in_progress",
                "completed": "closed", "cancelled": "closed", "reopened": "rejected"}
PRIORITY = {"SEVERE": 1, "MODERATE": 2, "MINOR": 3}
WORKFLOW_KEYS = (
    "status", "payment_status", "evidence_note", "evidence_filename", "evidence_path",
    "comparison_id", "verification", "completion_notes", "assignee_name", "location",
    "damage_type", "severity", "notes",
)
RELOAD_SECONDS = 5.0
_last_reload = 0.0


def enabled() -> bool:
    try:
        from flask import current_app, has_app_context

        from app.services.offline_store import offline_mode_enabled

        return (
            has_app_context()
            and not current_app.config.get("TESTING")
            and bool(current_app.config.get("DATABASE_CONFIGURED", True))
            and not offline_mode_enabled()
        )
    except Exception:
        return False


def _valid_user(db, text, user_id) -> int | None:
    if user_id is None:
        return None
    try:
        found = db.session.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": int(user_id)}).first()
        return int(user_id) if found else None
    except (TypeError, ValueError):
        return None


def save(row: dict[str, Any]) -> int | None:
    """Insert or update the job; returns its task_id (None when not persisted)."""
    if not enabled() or row.get("inspection_id") is None:
        return None
    from sqlalchemy import text

    from app.extensions import db

    try:
        insp = db.session.execute(
            text("SELECT municipality_id, ward_id FROM inspections WHERE inspection_id = :id"),
            {"id": row["inspection_id"]},
        ).first()
        if insp is None:
            return None
        detection_id = row.get("detection_id")
        if detection_id is not None and db.session.execute(
            text("SELECT 1 FROM detections WHERE detection_id = :id"), {"id": detection_id}
        ).first() is None:
            detection_id = None
        assignee = _valid_user(db, text, row.get("assignee_id"))
        status = row.get("status") or "open"
        now = datetime.utcnow()
        workflow = {k: row.get(k) for k in WORKFLOW_KEYS}
        params = {
            "inspection_id": row["inspection_id"],
            "detection_id": detection_id,
            "municipality_id": insp[0],
            "ward_id": insp[1],
            "title": f"{row.get('damage_type') or 'Road damage'} repair — {row.get('location') or ''}"[:255],
            "description": row.get("notes"),
            "priority": PRIORITY.get(str(row.get("severity") or "").upper(), 2),
            "status": STATUS_TO_DB.get(status, "pending"),
            "assignee": assignee,
            "lat": row.get("latitude"),
            "lng": row.get("longitude"),
            "started": now if status in {"in_progress", "evidence_uploaded"} else None,
            "completed": now if status in {"verified", "closed", "paid"} else None,
            "materials": json.dumps({"app_workflow": workflow}, default=str),
            "now": now,
        }
        task_id = row.get("task_id")
        if task_id:
            db.session.execute(
                text(
                    "UPDATE tasks SET detection_id = :detection_id, title = :title, "
                    "description = :description, priority = :priority, status = :status, "
                    "assigned_to = :assignee, contractor_id = :assignee, "
                    "assigned_at = CASE WHEN :assignee IS NOT NULL AND assigned_to IS DISTINCT FROM :assignee "
                    "THEN :now ELSE assigned_at END, "
                    "location_latitude = :lat, location_longitude = :lng, "
                    "started_at = COALESCE(started_at, :started), "
                    "completed_at = COALESCE(:completed, completed_at), "
                    "repair_materials = CAST(:materials AS jsonb), updated_at = :now "
                    "WHERE task_id = :task_id"
                ),
                {**params, "task_id": task_id},
            )
        else:
            task_id = db.session.execute(
                text(
                    "INSERT INTO tasks (task_code, inspection_id, detection_id, municipality_id, "
                    "ward_id, task_type, title, description, priority, status, assigned_to, "
                    "contractor_id, assigned_at, location_latitude, location_longitude, "
                    "location_geometry, started_at, completed_at, repair_materials, created_at, "
                    "updated_at) VALUES (:code, :inspection_id, :detection_id, :municipality_id, "
                    ":ward_id, 'repair', :title, :description, :priority, :status, :assignee, "
                    ":assignee, CASE WHEN :assignee IS NULL THEN NULL ELSE :now END, :lat, :lng, "
                    "CASE WHEN :lat IS NULL OR :lng IS NULL THEN NULL ELSE "
                    "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) END, :started, :completed, "
                    "CAST(:materials AS jsonb), :now, :now) RETURNING task_id"
                ),
                {**params, "code": f"RPR-{row['inspection_id']}-{uuid.uuid4().hex[:8].upper()}"},
            ).scalar_one()
        db.session.commit()
        return int(task_id)
    except Exception:
        logger.exception("Persisting repair job %s failed", row.get("id"))
        db.session.rollback()
        return None


def load_all() -> list[dict[str, Any]] | None:
    """All repair jobs from the DB as workflow rows (None when DB unavailable)."""
    if not enabled():
        return None
    from sqlalchemy import text

    from app.extensions import db

    try:
        rows = db.session.execute(
            text(
                "SELECT task_id, inspection_id, detection_id, status, assigned_to, title, "
                "description, priority, location_latitude, location_longitude, repair_materials, "
                "created_at, updated_at FROM tasks WHERE task_type = 'repair' ORDER BY task_id"
            )
        ).mappings().all()
    except Exception:
        logger.exception("Loading repair jobs failed")
        db.session.rollback()
        return None
    out = []
    for r in rows:
        meta = (r["repair_materials"] or {}).get("app_workflow") or {}
        status = meta.get("status") or DB_TO_STATUS.get(r["status"], "open")
        out.append(
            {
                "id": r["task_id"],
                "repair_id": r["task_id"],
                "task_id": r["task_id"],
                "inspection_id": r["inspection_id"],
                "detection_id": r["detection_id"],
                "location": meta.get("location") or "Not specified",
                "damage_type": meta.get("damage_type") or "unspecified",
                "severity": meta.get("severity") or {1: "SEVERE", 3: "MINOR"}.get(r["priority"], "MODERATE"),
                "status": status,
                "assignee_id": r["assigned_to"],
                "contractor_user_id": r["assigned_to"],
                "assignee_name": meta.get("assignee_name"),
                "notes": meta.get("notes") if meta.get("notes") is not None else r["description"],
                "evidence_note": meta.get("evidence_note"),
                "evidence_filename": meta.get("evidence_filename"),
                "evidence_path": meta.get("evidence_path"),
                "comparison_id": meta.get("comparison_id"),
                "verification": meta.get("verification"),
                "latitude": float(r["location_latitude"]) if r["location_latitude"] is not None else None,
                "longitude": float(r["location_longitude"]) if r["location_longitude"] is not None else None,
                "payment_status": meta.get("payment_status") or "pending",
                "completion_notes": meta.get("completion_notes"),
                "created_at": r["created_at"].isoformat() + "Z" if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() + "Z" if r["updated_at"] else None,
            }
        )
    return out


def due_for_reload() -> bool:
    return enabled() and time.monotonic() - _last_reload >= RELOAD_SECONDS


def mark_reloaded() -> None:
    global _last_reload
    _last_reload = time.monotonic()
