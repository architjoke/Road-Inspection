"""Repair job workflow (in-memory; optional reflected DB ignored if schema unknown)."""

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


def clear_repairs() -> None:
    global _next_id
    with _lock:
        _repairs.clear()
        _next_id = 1


def list_repairs(
    *,
    assignee_id: int | None = None,
    status: str | None = None,
    history: bool = False,
) -> list[dict[str, Any]]:
    with _lock:
        rows = [deepcopy(item) for item in _repairs.values()]
    if assignee_id is not None:
        rows = [
            r
            for r in rows
            if r.get("assignee_id") == assignee_id
            or r.get("contractor_user_id") == assignee_id
        ]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    elif history:
        rows = [r for r in rows if r.get("status") in DONE_STATUSES]
    rows.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return rows


def get_repair(repair_id: int) -> dict[str, Any] | None:
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
    now = datetime.utcnow().isoformat() + "Z"
    with _lock:
        pk = _next_id
        _next_id += 1
        status = "assigned" if assignee_id else "open"
        row = {
            "id": pk,
            "repair_id": pk,
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
        _repairs[pk] = row
        return deepcopy(row)


def update_repair(repair_id: int, **fields) -> dict[str, Any] | None:
    with _lock:
        row = _repairs.get(int(repair_id))
        if row is None:
            return None
        for key, value in fields.items():
            if value is not None or key in {"evidence_note", "comparison_id", "assignee_id"}:
                row[key] = value
        if "assignee_id" in fields:
            aid = fields.get("assignee_id")
            row["assignee_id"] = aid
            row["contractor_user_id"] = aid
            if aid and row.get("status") == "open":
                row["status"] = "assigned"
        row["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return deepcopy(row)


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
