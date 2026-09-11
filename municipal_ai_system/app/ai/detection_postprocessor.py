"""Normalize detections and assign severity / priority."""

from __future__ import annotations

from typing import Any


def infer_severity(detection: dict[str, Any]) -> str:
    damage = (detection.get("damage_type") or detection.get("class_name") or "").lower()
    conf = float(detection.get("confidence") or 0)

    # Optional Phase 8/9 features (ignored when absent)
    area_pct = detection.get("damaged_area_percentage")
    rel_depth = detection.get("mean_relative_depth")

    if "pothole" in damage:
        if area_pct is not None and float(area_pct) >= 5.0:
            return "SEVERE"
        if rel_depth is not None and float(rel_depth) >= 0.75:
            return "SEVERE"
        return "SEVERE" if conf >= 0.35 else "MODERATE"
    if "alligator" in damage:
        if area_pct is not None and float(area_pct) >= 8.0:
            return "SEVERE"
        return "SEVERE" if conf >= 0.45 else "MODERATE"
    if "crack" in damage:
        if conf >= 0.6 or (area_pct is not None and float(area_pct) >= 3.0):
            return "MODERATE"
        return "MINOR"
    if conf >= 0.7:
        return "MODERATE"
    return "MINOR"


def infer_priority(severity: str) -> int:
    mapping = {"SEVERE": 1, "MODERATE": 2, "MINOR": 3}
    return mapping.get(severity.upper(), 3)


def build_severity_evidence(detection: dict[str, Any]) -> dict[str, Any]:
    """Feature bag for future severity models — relative depth is never metric."""
    return {
        "class_name": detection.get("class_name") or detection.get("damage_type"),
        "confidence": detection.get("confidence"),
        "bbox_xyxy": detection.get("bbox_xyxy"),
        "mask_pixel_count": detection.get("mask_pixel_count"),
        "damaged_area_percentage": detection.get("damaged_area_percentage"),
        "mean_relative_depth": detection.get("mean_relative_depth"),
        "median_relative_depth": detection.get("median_relative_depth"),
        "segmentation_available": bool(
            detection.get("mask_pixel_count") is not None
            or detection.get("damaged_area_percentage") is not None
        ),
        "depth_available": bool(detection.get("mean_relative_depth") is not None),
        "calibration_available": False,
        "metric_depth_available": False,
        "area_unit": "pixels" if detection.get("mask_pixel_count") is not None else None,
    }


def enrich_detections(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for det in detections:
        item = dict(det)
        severity = infer_severity(item)
        item["severity"] = severity
        item["priority"] = infer_priority(severity)
        item["status"] = "PENDING_REVIEW"
        item["severity_evidence"] = build_severity_evidence(item)
        enriched.append(item)
    return enriched


def attach_segmentation_features(
    detections: list[dict[str, Any]],
    segmentation_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy mask area metrics onto matched detections, then re-enrich severity."""
    by_match: dict[Any, dict[str, Any]] = {}
    for seg in segmentation_results:
        key = seg.get("matched_detection_id")
        if key is None and seg.get("matched_detection_index") is not None:
            key = seg.get("matched_detection_index")
        if key is not None:
            by_match[key] = seg

    updated: list[dict[str, Any]] = []
    for idx, det in enumerate(detections):
        item = dict(det)
        key = item.get("detection_id", idx)
        seg = by_match.get(key) or by_match.get(idx)
        if seg:
            item["mask_pixel_count"] = seg.get("mask_pixel_count")
            item["damaged_area_percentage"] = seg.get("damaged_area_percentage")
            item["segmentation_model_version"] = seg.get("model_version")
        updated.append(item)
    return enrich_detections(updated)


def attach_depth_features(
    detections: list[dict[str, Any]],
    depth_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    regions = {
        r.get("matched_detection_id"): r for r in (depth_payload.get("per_region") or [])
    }
    updated: list[dict[str, Any]] = []
    for idx, det in enumerate(detections):
        item = dict(det)
        key = item.get("detection_id", idx)
        region = regions.get(key) or regions.get(idx)
        if region:
            item["mean_relative_depth"] = region.get("mean_relative_depth")
            item["median_relative_depth"] = region.get("median_relative_depth")
            item["min_relative_depth"] = region.get("min_relative_depth")
            item["max_relative_depth"] = region.get("max_relative_depth")
            item["depth_model_version"] = depth_payload.get("model_version")
            item["depth_normalization"] = "relative"
            item["metric_depth_available"] = False
        updated.append(item)
    return enrich_detections(updated)
