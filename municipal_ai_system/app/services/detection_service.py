"""Detection persistence — in-memory for offline tests; optional DB when reflected."""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_memory_detections: list[dict[str, Any]] = []
_next_id = 1

# Canonical fields stored in the offline repository / expected of DB rows.
REQUIRED_DETECTION_FIELDS = (
    "inspection_id",
    "damage_type",
    "confidence",
    "bounding_box",
    "source_width",
    "source_height",
    "frame_number",
    "model_version",
    "created_at",
)


def persistence_mode() -> str:
    """Describe active persistence backend without contacting RDS."""
    try:
        from app.models import get_model

        Detection = get_model("detections")
        if Detection is not None and hasattr(Detection, "__table__"):
            return "memory+reflected_db"
    except Exception:
        pass
    return "memory_only"


def clear_memory_detections() -> None:
    global _next_id
    with _lock:
        _memory_detections.clear()
        _next_id = 1


def list_memory_detections(inspection_id: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = _memory_detections
        if inspection_id is not None:
            rows = [r for r in rows if r.get("inspection_id") == inspection_id]
        return deepcopy(rows)


def _normalize_db_detection_row(row: Any, columns: set[str]) -> dict[str, Any]:
    data = dict(row) if not hasattr(row, "_mapping") else dict(row._mapping)
    conf = (
        data.get("confidence_score")
        if "confidence_score" in data
        else data.get("confidence")
    )
    damage = (
        data.get("damage_type")
        or data.get("defect_type")
        or data.get("class_name")
        or data.get("defect_class")
    )
    det_id = data.get("detection_id") or data.get("id")
    bbox = data.get("bounding_box") or data.get("bbox") or data.get("bbox_json") or {}
    return {
        "detection_id": det_id,
        "inspection_id": data.get("inspection_id"),
        "media_id": data.get("media_id"),
        "damage_type": damage,
        "class_name": damage,
        "confidence": conf,
        "severity": data.get("severity") or data.get("severity_level"),
        "priority": data.get("priority") or data.get("priority_level"),
        "status": data.get("status") or data.get("review_status") or "PENDING_REVIEW",
        "bounding_box": bbox if isinstance(bbox, dict) else {},
        "model_version": data.get("model_version"),
        "created_at": (
            data.get("created_at").isoformat() + "Z"
            if hasattr(data.get("created_at"), "isoformat")
            else data.get("created_at")
        ),
        "source": "db",
    }


def try_list_detections_db(inspection_id: int | None = None) -> list[dict[str, Any]]:
    """Best-effort read from reflected detections table."""
    try:
        from sqlalchemy import select

        from app.extensions import db
        from app.models import get_model
    except Exception:
        return []

    Detection = get_model("detections")
    if Detection is None or not hasattr(Detection, "__table__"):
        return []

    table = Detection.__table__
    columns = {c.name for c in table.columns}
    try:
        stmt = select(table)
        if inspection_id is not None and "inspection_id" in columns:
            stmt = stmt.where(table.c.inspection_id == inspection_id)
        stmt = stmt.limit(2000)
        rows = db.session.execute(stmt).mappings().all()
        return [_normalize_db_detection_row(r, columns) for r in rows]
    except Exception:
        logger.exception("Could not list detections from DB")
        try:
            db.session.rollback()
        except Exception:
            pass
        return []


def rehydrate_memory_from_tasks() -> int:
    """Pull detections from successful analysis tasks into memory if missing."""
    global _next_id
    try:
        from app.workers.task_store import TASK_SUCCEEDED, task_store
    except Exception:
        return 0

    added = 0
    existing_keys = {
        (r.get("inspection_id"), r.get("detection_id"))
        for r in list_memory_detections()
    }
    for task in task_store.list_all():
        if task.status != TASK_SUCCEEDED or not task.result_summary:
            continue
        dets = task.result_summary.get("detections") or []
        if not dets:
            continue
        insp_id = task.inspection_id
        if any(k[0] == insp_id for k in existing_keys):
            continue
        normalized = []
        for d in dets:
            item = dict(d)
            if "bbox_xyxy" not in item and item.get("bounding_box"):
                bb = item["bounding_box"]
                item["bbox_xyxy"] = [
                    bb.get("x1"),
                    bb.get("y1"),
                    bb.get("x2"),
                    bb.get("y2"),
                ]
            if item.get("bbox_xyxy") and all(v is not None for v in item["bbox_xyxy"]):
                normalized.append(item)
        if normalized:
            saved = save_detections_memory(insp_id, task.media_id, normalized)
            added += len(saved)
            for s in saved:
                existing_keys.add((insp_id, s.get("detection_id")))
            continue
        for d in dets:
            with _lock:
                row = {
                    "detection_id": _next_id,
                    "inspection_id": insp_id,
                    "media_id": task.media_id,
                    "damage_type": d.get("damage_type") or d.get("class_name"),
                    "class_name": d.get("class_name") or d.get("damage_type"),
                    "confidence": d.get("confidence"),
                    "severity": d.get("severity"),
                    "priority": d.get("priority"),
                    "status": d.get("status", "PENDING_REVIEW"),
                    "bounding_box": d.get("bounding_box") or {},
                    "model_version": d.get("model_version"),
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
                _memory_detections.append(row)
                _next_id += 1
                added += 1
                existing_keys.add((insp_id, row["detection_id"]))
    return added


def list_detections_merged(inspection_id: int | None = None) -> list[dict[str, Any]]:
    """Memory first, then hydrate from tasks, then DB rows not already present."""
    rehydrate_memory_from_tasks()
    rows = list_memory_detections(inspection_id=inspection_id)
    seen = {
        (r.get("inspection_id"), r.get("detection_id"))
        for r in rows
        if r.get("detection_id") is not None
    }
    # Also key by inspection+damage+confidence to reduce DB duplicates
    soft_seen = {
        (
            r.get("inspection_id"),
            str(r.get("damage_type") or r.get("class_name") or "").lower(),
            round(float(r.get("confidence") or 0), 3),
        )
        for r in rows
    }
    for db_row in try_list_detections_db(inspection_id=inspection_id):
        key = (db_row.get("inspection_id"), db_row.get("detection_id"))
        soft = (
            db_row.get("inspection_id"),
            str(db_row.get("damage_type") or "").lower(),
            round(float(db_row.get("confidence") or 0), 3),
        )
        if key in seen or soft in soft_seen:
            continue
        rows.append(db_row)
        seen.add(key)
        soft_seen.add(soft)
    rows.sort(key=lambda r: int(r.get("detection_id") or 0), reverse=True)
    return rows


def save_detections_memory(
    inspection_id: int,
    media_id: int | None,
    detections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    global _next_id
    saved: list[dict[str, Any]] = []
    with _lock:
        for det in detections:
            row = {
                "detection_id": _next_id,
                "inspection_id": inspection_id,
                "media_id": media_id,
                "damage_type": det.get("damage_type") or det.get("class_name"),
                "class_id": det.get("class_id"),
                "class_name": det.get("class_name"),
                "confidence": det.get("confidence"),
                "severity": det.get("severity"),
                "priority": det.get("priority"),
                "status": det.get("status", "PENDING_REVIEW"),
                "bounding_box": {
                    "x1": det["bbox_xyxy"][0],
                    "y1": det["bbox_xyxy"][1],
                    "x2": det["bbox_xyxy"][2],
                    "y2": det["bbox_xyxy"][3],
                    "normalized": det.get("bbox_normalized"),
                },
                "frame_number": det.get("frame_number"),
                "model_version": det.get("model_version"),
                "source_width": det.get("source_width"),
                "source_height": det.get("source_height"),
                "mask_pixel_count": det.get("mask_pixel_count"),
                "damaged_area_percentage": det.get("damaged_area_percentage"),
                "mean_relative_depth": det.get("mean_relative_depth"),
                "median_relative_depth": det.get("median_relative_depth"),
                "severity_score": det.get("severity_score"),
                "recommended_action": det.get("recommended_action"),
                "repair_type": det.get("repair_type"),
                "manual_review_required": det.get("manual_review_required"),
                "severity_assessment": det.get("severity_assessment"),
                "severity_evidence": det.get("severity_evidence"),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
            _memory_detections.append(row)
            saved.append(deepcopy(row))
            _next_id += 1
    return saved


def try_persist_detections_db(
    inspection_id: int,
    media_id: int | None,
    detections: list[dict[str, Any]],
    latitude: float | None = None,
    longitude: float | None = None,
) -> int:
    """
    Best-effort insert into reflected `detections` table.
    Returns number of rows inserted; 0 if schema/DB unavailable.
    """
    try:
        from geoalchemy2.elements import WKTElement
        from sqlalchemy import insert

        from app.extensions import db
        from app.models import get_model
    except Exception:
        return 0

    Detection = get_model("detections")
    if Detection is None or not hasattr(Detection, "__table__"):
        return 0

    columns = {c.name for c in Detection.__table__.columns}
    inserted = 0

    for det in detections:
        values: dict[str, Any] = {}
        if "inspection_id" in columns:
            values["inspection_id"] = inspection_id
        if "media_id" in columns and media_id is not None:
            values["media_id"] = media_id
        for key, candidates in {
            "damage_type": ["damage_type", "defect_type", "defect_class", "class_name"],
            "severity": ["severity", "severity_level"],
            "priority": ["priority", "priority_level"],
            "status": ["status", "review_status"],
            "confidence_score": ["confidence_score", "confidence", "score"],
        }.items():
            for candidate in candidates:
                if candidate in columns:
                    if key == "damage_type":
                        values[candidate] = det.get("damage_type") or det.get("class_name")
                    elif key == "severity":
                        values[candidate] = det.get("severity")
                    elif key == "priority":
                        values[candidate] = det.get("priority")
                    elif key == "status":
                        values[candidate] = det.get("status", "PENDING_REVIEW")
                    elif key == "confidence_score":
                        values[candidate] = det.get("confidence")
                    break

        bbox_cols = [c for c in ("bounding_box", "bbox", "bbox_json") if c in columns]
        if bbox_cols:
            values[bbox_cols[0]] = {
                "x1": det["bbox_xyxy"][0],
                "y1": det["bbox_xyxy"][1],
                "x2": det["bbox_xyxy"][2],
                "y2": det["bbox_xyxy"][3],
            }

        geom_cols = [
            c
            for c in ("location_geometry", "gps_location", "geom", "geometry")
            if c in columns
        ]
        if geom_cols and latitude is not None and longitude is not None:
            values[geom_cols[0]] = WKTElement(
                f"POINT({longitude} {latitude})",
                srid=4326,
            )

        if "created_at" in columns:
            values["created_at"] = datetime.utcnow()

        if not values:
            continue

        try:
            db.session.execute(insert(Detection.__table__).values(**values))
            inserted += 1
        except Exception:
            logger.exception("Failed to insert detection row for inspection %s", inspection_id)
            db.session.rollback()
            return inserted

    try:
        if inserted:
            db.session.commit()
    except Exception:
        logger.exception("Commit failed for detections of inspection %s", inspection_id)
        db.session.rollback()
        return 0

    return inserted


def update_memory_detection(detection_id: int, **fields) -> dict[str, Any] | None:
    with _lock:
        for row in _memory_detections:
            if int(row.get("detection_id") or -1) != int(detection_id):
                continue
            for key, value in fields.items():
                if value is not None:
                    row[key] = value
            row["updated_at"] = datetime.utcnow().isoformat() + "Z"
            return deepcopy(row)
    return None


def persist_detections(
    inspection_id: int,
    media_id: int | None,
    detections: list[dict[str, Any]],
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    memory_rows = save_detections_memory(inspection_id, media_id, detections)
    db_count = 0
    try:
        db_count = try_persist_detections_db(
            inspection_id, media_id, detections, latitude, longitude
        )
    except Exception:
        logger.exception("DB detection persistence skipped/failed")

    return {
        "memory_count": len(memory_rows),
        "db_count": db_count,
        "detections": memory_rows,
    }
