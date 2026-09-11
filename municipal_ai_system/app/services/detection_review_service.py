"""Detection review helpers for Phase 10 severity display (offline-safe)."""

from __future__ import annotations

from typing import Any

from app.services.detection_service import list_memory_detections, persistence_mode


def serialize_detection_for_review(row: dict[str, Any]) -> dict[str, Any]:
    """Additive JSON shape for review UI / API — never claims metric depth."""
    assessment = row.get("severity_assessment") or {}
    evidence = assessment.get("evidence") or row.get("severity_evidence") or {}
    bbox = row.get("bounding_box") or {}

    return {
        "detection_id": row.get("detection_id"),
        "inspection_id": row.get("inspection_id"),
        "media_id": row.get("media_id"),
        "damage_type": row.get("damage_type") or row.get("class_name"),
        "class_name": row.get("class_name"),
        "confidence": row.get("confidence"),
        "severity": row.get("severity"),
        "priority": row.get("priority"),
        "severity_score": row.get("severity_score") or assessment.get("score"),
        "recommended_action": row.get("recommended_action")
        or assessment.get("recommended_action"),
        "repair_type": row.get("repair_type") or assessment.get("repair_type"),
        "manual_review_required": bool(
            row.get("manual_review_required")
            or assessment.get("manual_review_required")
        ),
        "status": row.get("status"),
        "bounding_box": bbox,
        "mask_pixel_count": row.get("mask_pixel_count")
        or evidence.get("mask_pixel_count"),
        "damaged_area_percentage": row.get("damaged_area_percentage")
        or evidence.get("damaged_area_percentage"),
        "mean_relative_depth": row.get("mean_relative_depth")
        or evidence.get("mean_relative_depth"),
        "depth_normalization": row.get("depth_normalization") or "relative",
        "metric_depth_available": False,
        "calibration_available": False,
        "area_unit": evidence.get("area_unit")
        or ("pixels" if row.get("mask_pixel_count") is not None else None),
        "severity_assessment": assessment or None,
        "severity_evidence": evidence or None,
        "model_version": row.get("model_version"),
        "frame_number": row.get("frame_number"),
        "created_at": row.get("created_at"),
        "explanation": assessment.get("explanation") or evidence.get("explanation"),
    }


def list_detections_for_review(
    inspection_id: int | None = None,
    *,
    manual_review_only: bool = False,
) -> dict[str, Any]:
    rows = list_memory_detections(inspection_id=inspection_id)
    items = [serialize_detection_for_review(r) for r in rows]
    if manual_review_only:
        items = [i for i in items if i.get("manual_review_required")]
    return {
        "persistence_mode": persistence_mode(),
        "count": len(items),
        "detections": items,
        "note": (
            "Relative depth is not metric. Physical mm/cm/m and volume require "
            "calibration (unavailable)."
        ),
    }


def get_detection_for_review(detection_id: int) -> dict[str, Any] | None:
    for row in list_memory_detections():
        if int(row.get("detection_id") or -1) == int(detection_id):
            return serialize_detection_for_review(row)
    return None
