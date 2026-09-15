"""Detection review helpers for severity display (offline-safe)."""

from __future__ import annotations

from typing import Any

from app.services.detection_service import list_detections_merged, persistence_mode


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
        "repair_id": row.get("repair_id"),
    }


def list_detections_for_review(
    inspection_id: int | None = None,
    *,
    manual_review_only: bool = False,
) -> dict[str, Any]:
    rows = list_detections_merged(inspection_id=inspection_id)
    # Attach linked open repair ids when present
    try:
        from app.services.repair_service import list_repairs

        repairs = list_repairs()
        by_detection = {
            r.get("detection_id"): r.get("id")
            for r in repairs
            if r.get("detection_id") is not None
        }
        for row in rows:
            rid = by_detection.get(row.get("detection_id"))
            if rid:
                row["repair_id"] = rid
    except Exception:
        pass

    items = [serialize_detection_for_review(r) for r in rows]
    if manual_review_only:
        items = [i for i in items if i.get("manual_review_required")]
    return {
        "persistence_mode": persistence_mode(),
        "count": len(items),
        "detections": items,
        "note": (
            "Detections come from live YOLO analysis. "
            "Unassigned repair jobs are auto-created for staff to assign to contractors."
        ),
    }


def get_detection_for_review(detection_id: int) -> dict[str, Any] | None:
    for row in list_detections_merged():
        if int(row.get("detection_id") or -1) != int(detection_id):
            continue
        # Attach linked repair id (same as list view) so assign updates, not duplicates.
        if not row.get("repair_id"):
            try:
                from app.services.repair_service import list_repairs

                for repair in list_repairs():
                    if repair.get("detection_id") == int(detection_id):
                        row["repair_id"] = repair.get("id")
                        break
            except Exception:
                pass
        return serialize_detection_for_review(row)
    return None
