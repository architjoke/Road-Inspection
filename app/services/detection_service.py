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


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


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
    if not bbox and data.get("bounding_box_x1") is not None:
        bbox = {
            "x1": data.get("bounding_box_x1"),
            "y1": data.get("bounding_box_y1"),
            "x2": data.get("bounding_box_x2"),
            "y2": data.get("bounding_box_y2"),
        }
    severity = data.get("severity") or data.get("severity_level")
    return {
        "detection_id": det_id,
        "inspection_id": data.get("inspection_id"),
        "media_id": data.get("media_id"),
        "damage_type": damage,
        "class_name": damage,
        "confidence": _num(conf),
        "severity": str(severity).upper() if severity else None,
        "priority": data.get("priority") or data.get("priority_level"),
        "status": data.get("status") or data.get("review_status") or "PENDING_REVIEW",
        "bounding_box": bbox if isinstance(bbox, dict) else {},
        "repair_type": data.get("recommended_repair_type"),
        "area_sq_meters": _num(data.get("area_sq_meters")),
        "estimated_depth_mm": _num(data.get("estimated_depth_mm")),
        "estimated_volume_m3": _num(data.get("estimated_volume_m3")),
        "estimated_cost": _num(data.get("estimated_repair_cost")),
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
    ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Cache detections in memory; ``ids`` (from the DB insert) keep IDs identical."""
    global _next_id
    saved: list[dict[str, Any]] = []
    use_ids = ids if ids and len(ids) == len(detections) else None
    with _lock:
        for index, det in enumerate(detections):
            row_id = use_ids[index] if use_ids else _next_id
            row = {
                "detection_id": row_id,
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
                "measurements": det.get("measurements"),
                "area_sq_meters": det.get("area_sq_meters"),
                "estimated_depth_mm": det.get("estimated_depth_mm"),
                "estimated_volume_m3": det.get("estimated_volume_m3"),
                "estimated_cost": det.get("estimated_cost"),
                "cost_estimate": det.get("cost_estimate"),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
            _memory_detections.append(row)
            saved.append(deepcopy(row))
            _next_id = max(_next_id, row_id) + 1
    return saved


def _db_insert_values(
    det: dict[str, Any],
    columns: set[str],
    *,
    inspection_id: int,
    media_id: int | None,
    latitude: float | None,
    longitude: float | None,
    ward_id: int | None = None,
) -> dict[str, Any]:
    """Map an enriched detection onto the live ``detections`` columns."""
    from uuid import uuid4

    severity = str(det.get("severity") or "MODERATE").lower()
    if severity not in {"minor", "moderate", "severe"}:
        severity = "moderate"
    priority = det.get("priority")
    try:
        priority = min(3, max(1, int(priority)))
    except (TypeError, ValueError):
        priority = 2
    bbox = det.get("bbox_xyxy") or [None, None, None, None]
    geom = (det.get("severity_assessment") or {}).get("evidence") or {}
    bw, bh = geom.get("bbox_width"), geom.get("bbox_height")
    now = datetime.utcnow()

    candidates: dict[str, Any] = {
        "inspection_id": inspection_id,
        "media_id": media_id,
        "detection_code": f"DET-{inspection_id}-{uuid4().hex[:10].upper()}",
        "damage_type": det.get("damage_type") or det.get("class_name") or "unknown",
        "damage_class": det.get("class_name"),
        "severity_level": severity,
        "severity": severity,
        "priority_level": priority,
        "priority": priority,
        "confidence_score": det.get("confidence") or 0.0,
        "bounding_box_x1": int(round(bbox[0])) if bbox[0] is not None else None,
        "bounding_box_y1": int(round(bbox[1])) if bbox[1] is not None else None,
        "bounding_box_x2": int(round(bbox[2])) if bbox[2] is not None else None,
        "bounding_box_y2": int(round(bbox[3])) if bbox[3] is not None else None,
        "area_sq_pixels": int(round(bw * bh)) if bw and bh else None,
        "area_sq_meters": det.get("area_sq_meters"),
        "estimated_depth_mm": det.get("estimated_depth_mm"),
        "estimated_volume_m3": det.get("estimated_volume_m3"),
        "estimated_repair_cost": det.get("estimated_cost"),
        "recommended_repair_type": (det.get("repair_type") or "")[:100] or None,
        "gps_latitude": latitude,
        "gps_longitude": longitude,
        "ward_id": ward_id,
        "status": det.get("status", "PENDING_REVIEW"),
        "is_verified": False,
        "is_false_positive": False,
        "detected_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values = {k: v for k, v in candidates.items() if k in columns and v is not None}

    if "bounding_box" in columns and bbox[0] is not None:
        values["bounding_box"] = {"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]}
    geom_cols = [
        c for c in ("location_geometry", "gps_location", "geom", "geometry") if c in columns
    ]
    if geom_cols and latitude is not None and longitude is not None:
        from geoalchemy2.elements import WKTElement

        values[geom_cols[0]] = WKTElement(f"POINT({longitude} {latitude})", srid=4326)
    return values


def try_persist_detections_db(
    inspection_id: int,
    media_id: int | None,
    detections: list[dict[str, Any]],
    latitude: float | None = None,
    longitude: float | None = None,
    ward_id: int | None = None,
) -> list[int]:
    """
    Insert into the reflected ``detections`` table (live Supabase schema).

    Returns the new ``detection_id`` values in input order ([] if the DB is
    unavailable or the insert failed). Rows are skipped
    when the inspection has no GPS, because ``gps_latitude``/``gps_longitude``
    are NOT NULL in the live schema.
    """
    try:
        from sqlalchemy import insert

        from app.extensions import db
        from app.models import get_model
    except Exception:
        return []

    Detection = get_model("detections")
    if Detection is None or not hasattr(Detection, "__table__"):
        return []

    table = Detection.__table__
    columns = {c.name for c in table.columns}
    required_gps = {"gps_latitude", "gps_longitude"} & {
        c.name for c in table.columns if not c.nullable
    }
    if required_gps and (latitude is None or longitude is None):
        logger.warning(
            "Inspection %s has no GPS; %s detection(s) kept in memory only",
            inspection_id,
            len(detections),
        )
        return []

    ids: list[int] = []
    try:
        for det in detections:
            values = _db_insert_values(
                det,
                columns,
                inspection_id=inspection_id,
                media_id=media_id,
                latitude=latitude,
                longitude=longitude,
                ward_id=ward_id,
            )
            if not values:
                return []
            new_id = db.session.execute(
                insert(table).values(**values).returning(table.c.detection_id)
            ).scalar_one()
            ids.append(int(new_id))
        if ids:
            db.session.commit()
    except Exception:
        logger.exception("Failed to persist detections for inspection %s", inspection_id)
        db.session.rollback()
        return []
    return ids


UNREVIEWED_STATUSES = {"PENDING_REVIEW", "pending_review", None, ""}


def discard_unreviewed(inspection_id: int, media_id: int | None) -> list[int]:
    """
    Before re-analysing media, drop its previous *unreviewed* detections (and the
    untouched auto-created repair jobs pointing at them) so a re-run replaces
    results instead of duplicating them. Reviewed / verified detections and
    jobs that were assigned or progressed are kept.
    Returns the detection ids removed.
    """
    global _memory_detections
    removed: set[int] = set()
    with _lock:
        keep = []
        for row in _memory_detections:
            same = row.get("inspection_id") == inspection_id and (
                media_id is None or row.get("media_id") in (None, media_id)
            )
            if same and row.get("status") in UNREVIEWED_STATUSES and not row.get("review_decision"):
                removed.add(int(row["detection_id"]))
            else:
                keep.append(row)
        _memory_detections[:] = keep

    try:
        from flask import current_app

        from app.services.offline_store import offline_mode_enabled

        use_db = not offline_mode_enabled() and not current_app.config.get("TESTING")
    except Exception:
        use_db = False
    if use_db:
        from sqlalchemy import text

        from app.extensions import db

        try:
            ids = db.session.execute(
                text(
                    "SELECT detection_id FROM detections WHERE inspection_id = :i "
                    "AND (:m IS NULL OR media_id = :m OR media_id IS NULL) "
                    "AND COALESCE(is_verified, FALSE) = FALSE "
                    "AND COALESCE(is_false_positive, FALSE) = FALSE "
                    "AND (status IS NULL OR status IN ('PENDING_REVIEW', 'pending_review')) "
                    "AND NOT EXISTS (SELECT 1 FROM detection_reviews r WHERE r.detection_id = detections.detection_id)"
                ),
                {"i": inspection_id, "m": media_id},
            ).scalars().all()
            if ids:
                db.session.execute(
                    text(
                        "DELETE FROM tasks WHERE task_type = 'repair' AND status = 'pending' "
                        "AND assigned_to IS NULL AND detection_id = ANY(:ids)"
                    ),
                    {"ids": list(ids)},
                )
                db.session.execute(
                    text("DELETE FROM detections WHERE detection_id = ANY(:ids)"), {"ids": list(ids)}
                )
                db.session.commit()
                removed.update(int(i) for i in ids)
        except Exception:
            logger.exception("Could not clear previous detections for inspection %s", inspection_id)
            db.session.rollback()

    if removed:
        from app.services.repair_service import discard_open_auto_repairs

        discard_open_auto_repairs(removed)
    return sorted(removed)


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
    ward_id: int | None = None,
) -> dict[str, Any]:
    # Database first, so the in-memory cache uses the same detection IDs that
    # reviews, repairs and reports will reference after a restart.
    db_ids: list[int] = []
    try:
        db_ids = try_persist_detections_db(
            inspection_id, media_id, detections, latitude, longitude, ward_id=ward_id
        )
    except Exception:
        logger.exception("DB detection persistence skipped/failed")
    memory_rows = save_detections_memory(inspection_id, media_id, detections, ids=db_ids)

    return {
        "memory_count": len(memory_rows),
        "db_count": len(db_ids),
        "detections": memory_rows,
    }
