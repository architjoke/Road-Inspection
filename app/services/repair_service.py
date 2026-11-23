"""Repair job workflow.

In-memory working set, written through to Supabase ``tasks`` (task_type
'repair') and reloaded from it every few seconds — see ``repair_persistence``.
Offline mode / tests use memory only.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime
from typing import Any

_lock = threading.RLock()
_repairs: dict[int, dict[str, Any]] = {}
_next_id = 1

OPEN_STATUSES = {"open", "assigned", "in_progress", "evidence_uploaded"}
DONE_STATUSES = {"verified", "closed", "rejected", "paid"}
# Jobs that cannot be stored (no inspection) get ids far from DB task ids.
MEMORY_ONLY_ID_BASE = 1_000_000


def clear_repairs() -> None:
    global _next_id
    with _lock:
        _repairs.clear()
        _next_id = 1


def _sync_from_db(force: bool = False) -> None:
    """Reload the working set from Supabase (source of truth when available)."""
    from app.services import repair_persistence

    if not (repair_persistence.enabled() if force else repair_persistence.due_for_reload()):
        return
    rows = repair_persistence.load_all()
    repair_persistence.mark_reloaded()
    if rows is None:
        return
    global _next_id
    with _lock:
        memory_only = {k: v for k, v in _repairs.items() if not v.get("task_id")}
        _repairs.clear()
        _repairs.update({row["id"]: row for row in rows})
        _repairs.update(memory_only)
        _next_id = max([_next_id, *[k + 1 for k in memory_only]])


def list_repairs(
    *,
    assignee_id: int | None = None,
    status: str | None = None,
    history: bool = False,
) -> list[dict[str, Any]]:
    _sync_from_db()
    with _lock:
        rows = [deepcopy(item) for item in _repairs.values()]
    if assignee_id is not None:
        try:
            want = int(assignee_id)
        except (TypeError, ValueError):
            want = assignee_id

        def _matches(row: dict[str, Any]) -> bool:
            for key in ("assignee_id", "contractor_user_id"):
                raw = row.get(key)
                if raw is None:
                    continue
                try:
                    if int(raw) == want:
                        return True
                except (TypeError, ValueError):
                    if raw == assignee_id:
                        return True
            return False

        rows = [r for r in rows if _matches(r)]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    elif history:
        rows = [r for r in rows if r.get("status") in DONE_STATUSES]
    rows.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return rows


def get_repair(repair_id: int) -> dict[str, Any] | None:
    _sync_from_db()
    with _lock:
        item = _repairs.get(int(repair_id))
        return deepcopy(item) if item else None


def create_repair(
    *,
    inspection_id: int | None = None,
    detection_id: int | None = None,
    location: str | None = None,
    damage_type: str | None = None,
    severity: str | None = None,
    assignee_id: int | None = None,
    assignee_name: str | None = None,
    notes: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    global _next_id
    from app.services import repair_persistence

    now = datetime.utcnow().isoformat() + "Z"
    with _lock:
        status = "assigned" if assignee_id else "open"
        row = {
            "id": None,
            "repair_id": None,
            "inspection_id": inspection_id,
            "detection_id": detection_id,
            "location": location or "Not specified",
            "damage_type": damage_type or "unspecified",
            "severity": (severity or "MODERATE").upper(),
            "status": status,
            "assignee_id": assignee_id,
            "contractor_user_id": assignee_id,
            "assignee_name": assignee_name,
            "notes": notes,
            "evidence_note": None,
            "evidence_filename": None,
            "evidence_path": None,
            "comparison_id": None,
            "latitude": latitude,
            "longitude": longitude,
            "payment_status": "pending",
            "completion_notes": None,
            "created_at": now,
            "updated_at": now,
        }
    task_id = repair_persistence.save(row)
    with _lock:
        if task_id:
            pk = task_id
            row["task_id"] = task_id
        else:
            if repair_persistence.enabled():
                _next_id = max(_next_id, MEMORY_ONLY_ID_BASE)
            pk = _next_id
            _next_id += 1
        row["id"] = row["repair_id"] = pk
        _repairs[pk] = row
        return deepcopy(row)


def update_repair(repair_id: int, **fields) -> dict[str, Any] | None:
    _sync_from_db()
    with _lock:
        known = int(repair_id) in _repairs
    if not known:
        _sync_from_db(force=True)
    with _lock:
        row = _repairs.get(int(repair_id))
        if row is None:
            return None
        for key, value in fields.items():
            if value is not None or key in {"evidence_note", "comparison_id", "assignee_id", "verification"}:
                row[key] = value
        if "assignee_id" in fields:
            aid = fields.get("assignee_id")
            row["assignee_id"] = aid
            row["contractor_user_id"] = aid
            if aid and row.get("status") == "open":
                row["status"] = "assigned"
        row["updated_at"] = datetime.utcnow().isoformat() + "Z"
        snapshot = deepcopy(row)
    if snapshot.get("task_id"):
        from app.services import repair_persistence

        repair_persistence.save(snapshot)
    return snapshot


def discard_open_auto_repairs(detection_ids: set[int]) -> int:
    """Remove untouched (open, unassigned) jobs for detections that were replaced."""
    with _lock:
        doomed = [
            pk for pk, row in _repairs.items()
            if row.get("detection_id") in detection_ids
            and row.get("status") == "open"
            and not row.get("assignee_id")
        ]
        for pk in doomed:
            _repairs.pop(pk, None)
    return len(doomed)


def repair_stats(*, assignee_id: int | None = None) -> dict[str, int]:
    rows = list_repairs(assignee_id=assignee_id)
    return {
        "assigned": len([r for r in rows if r.get("status") in {"open", "assigned"}]),
        "in_progress": len([r for r in rows if r.get("status") == "in_progress"]),
        "completed": len([r for r in rows if r.get("status") in DONE_STATUSES]),
        "evidence_due": len(
            [r for r in rows if r.get("status") in {"assigned", "in_progress"}]
        ),
        "total": len(rows),
    }
