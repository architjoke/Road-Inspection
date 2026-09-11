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
