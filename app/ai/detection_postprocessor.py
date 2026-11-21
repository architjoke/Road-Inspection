"""Normalize detections and assign severity / priority (Phase 10 engine)."""

from __future__ import annotations

from typing import Any

from app.ai.severity_engine import (
    apply_severity,
    assess_severity,
    build_severity_evidence,
    enrich_detections,
    infer_priority,
    infer_severity,
    summarize_severities,
)


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

    union_pct = None
    if segmentation_results:
        # Prefer explicit union if provided on any result payload parent — callers
        # may pass results only; compute naive max as soft hint when no union given.
        try:
            union_pct = max(
                float(s.get("damaged_area_percentage") or 0.0)
                for s in segmentation_results
            )
        except Exception:
            union_pct = None

    return enrich_detections(updated, union_damaged_area_percentage=union_pct)


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
            item["calibration_available"] = False
        updated.append(item)
    return enrich_detections(updated)


__all__ = [
    "infer_severity",
    "infer_priority",
    "enrich_detections",
    "build_severity_evidence",
    "assess_severity",
    "apply_severity",
    "summarize_severities",
    "attach_segmentation_features",
    "attach_depth_features",
]
