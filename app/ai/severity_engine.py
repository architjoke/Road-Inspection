"""Phase 10 damage severity engine — v2, graded on estimated physical size.

Pipeline per detection:

1. Project the bounding box onto the road plane (``ground_geometry``) to get
   width / length in metres (assumed camera pose, EXIF focal length if known).
2. Refine area with the segmentation mask fill ratio when segmentation ran.
3. Grade by family: pothole diameter (mm), alligator area (m2), crack length (m).
4. Relative depth (if enabled) can raise a pothole/alligator one level.
5. Model confidence never sets severity — it only triggers manual review.

If the size estimate is unreliable (damage near the horizon, too far away),
the engine falls back to image-fraction thresholds and flags manual review.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.ai.ground_geometry import build_camera, measure_bbox
from app.ai.severity_rules import (
    SEVERITY_LEVELS,
    classify_damage_family,
    get_severity_rules,
)

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def bbox_geometry(detection: dict[str, Any]) -> dict[str, float | None]:
    bbox = detection.get("bbox_xyxy") or []
    if len(bbox) != 4:
        return {
            "width": None,
            "height": None,
            "diagonal": None,
            "area": None,
            "norm_area": None,
            "aspect_ratio": None,
        }
    x1, y1, x2, y2 = [float(v) for v in bbox]
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    area = width * height
    src_w = _safe_float(detection.get("source_width")) or 0.0
    src_h = _safe_float(detection.get("source_height")) or 0.0
    denom = max(1.0, src_w * src_h) if src_w and src_h else None
    norm = (area / denom) if denom else None
    aspect = (max(width, height) / max(1e-6, min(width, height))) if width and height else None
    diagonal = (width**2 + height**2) ** 0.5
    return {
        "width": width,
        "height": height,
        "diagonal": diagonal,
        "area": area,
        "norm_area": norm,
        "aspect_ratio": aspect,
    }


def infer_priority(severity: str, rules: dict[str, Any] | None = None) -> int:
    rules = rules or get_severity_rules()
    mapping = rules.get("priority_map") or {"SEVERE": 1, "MODERATE": 2, "MINOR": 3}
    return int(mapping.get(str(severity).upper(), 3))


def _level_rank(level: str) -> int:
    return {"MINOR": 0, "MODERATE": 1, "SEVERE": 2}.get(str(level).upper(), 0)


def _bump(level: str) -> str:
    return SEVERITY_LEVELS[min(2, _level_rank(level) + 1)]


def _grade(value: float, moderate: float, severe: float) -> tuple[str, float]:
    """Level + continuous score (MINOR 0-0.42, MODERATE 0.42-0.72, SEVERE 0.72-1)."""
    moderate = max(moderate, 1e-9)
    severe = max(severe, moderate + 1e-9)
    if value >= severe:
        return "SEVERE", min(1.0, 0.72 + 0.28 * min(1.0, (value - severe) / severe))
    if value >= moderate:
        return "MODERATE", 0.42 + 0.30 * (value - moderate) / (severe - moderate)
    return "MINOR", 0.42 * max(0.0, value) / moderate


def _camera_for(detection: dict[str, Any]) -> dict[str, Any] | None:
    width = _safe_float(detection.get("source_width"))
    height = _safe_float(detection.get("source_height"))
    if not width or not height:
        return None
    meta = detection.get("camera") or {}
    return build_camera(
        width,
        height,
        focal_length_35mm=_safe_float(meta.get("focal_length_35mm")),
        height_m=_safe_float(meta.get("height_m")),
        pitch_deg=_safe_float(meta.get("pitch_deg")),
        video=bool(meta.get("video")),
    )


def estimate_measurements(
    detection: dict[str, Any],
    family: str,
    class_rules: dict[str, Any],
    geom: dict[str, Any],
) -> dict[str, Any]:
    """Physical size estimate for one detection (all values are estimates)."""
    out: dict[str, Any] = {
        "reliable": False,
        "size_basis": "unavailable",
        "width_m": None,
        "length_m": None,
        "area_m2": None,
        "diameter_mm": None,
        "crack_length_m": None,
        "distance_m": None,
        "fill_factor": None,
        "fill_source": None,
        "reasons": [],
        "camera": None,
    }
    cam = _camera_for(detection)
    bbox = detection.get("bbox_xyxy") or []
    if cam is None or len(bbox) != 4:
        out["reasons"].append("missing_image_size_or_bbox")
        return out

    ground = measure_bbox(bbox, cam)
    out["camera"] = ground.get("camera")
    out["reasons"].extend(ground.get("reasons") or [])
    out["distance_m"] = ground.get("distance_m")
    if ground.get("width_m") is None:
        return out

    width_m = float(ground["width_m"])
    length_m = float(ground["length_m"])

    # A box bigger than any real defect of this kind means a bad box or a photo the
    # ground-plane model cannot measure (e.g. a top-down close-up): fall back to the
    # typical-size path and send it to review instead of pricing a huge patch.
    max_side = _safe_float(class_rules.get("max_plausible_m"))
    if max_side and max(width_m, length_m) > max_side:
        out["reasons"].append("implausible_size")
        out["rejected_size_m"] = [round(width_m, 2), round(length_m, 2)]
        return out

    # Area fill: segmentation mask coverage of the box beats a shape constant.
    mask_px = _safe_float(detection.get("mask_pixel_count"))
    box_px = geom.get("area") or 0.0
    if mask_px and box_px > 0:
        fill = min(1.0, max(0.05, mask_px / box_px))
        out["fill_source"] = "segmentation_mask"
    else:
        fill = float(class_rules.get("fill_factor", 1.0))
        out["fill_source"] = "shape_assumption"
    out["fill_factor"] = round(fill, 3)

    long_side, short_side = max(width_m, length_m), min(width_m, length_m)
    aspect = long_side / max(short_side, 1e-6)
    # Thin boxes: crack runs along the long side; squarish boxes: diagonal crack.
    crack_length = long_side if aspect >= 3.0 else math.hypot(width_m, length_m)

    out.update(
        {
            "reliable": bool(ground.get("reliable")),
            "size_basis": "ground_plane_estimate",
            "width_m": round(width_m, 3),
            "length_m": round(length_m, 3),
            "area_m2": round(width_m * length_m * fill, 4),
            "diameter_mm": round((width_m + length_m) / 2.0 * 1000.0, 1),
            "crack_length_m": round(crack_length, 3),
        }
    )
    return out


def _measure_value(measurements: dict[str, Any], measure: str) -> float | None:
    key = {"diameter_mm": "diameter_mm", "area_m2": "area_m2", "length_m": "crack_length_m"}.get(
        measure
    )
    return _safe_float(measurements.get(key)) if key else None


def assess_severity(
    detection: dict[str, Any],
    *,
    rules: dict[str, Any] | None = None,
    union_damaged_area_percentage: float | None = None,
) -> dict[str, Any]:
    """Severity assessment for one detection (size-based; confidence -> review only)."""
    rules = rules or get_severity_rules()
    family = classify_damage_family(detection.get("class_name"), detection.get("damage_type"))
    class_rules = (rules.get("classes") or {}).get(family) or rules["classes"]["default"]

    confidence = _safe_float(detection.get("confidence"), 0.0) or 0.0
    geom = bbox_geometry(detection)
    area_pct = _safe_float(detection.get("damaged_area_percentage"))
    mask_pixels = detection.get("mask_pixel_count")
    rel_depth = _safe_float(detection.get("mean_relative_depth"))

    segmentation_available = area_pct is not None or mask_pixels is not None
    depth_available = rel_depth is not None

    signals_used: list[str] = ["damage_class"]
    unavailable: list[str] = ["metric_depth", "physical_volume"]
    review_reasons: list[str] = []

    measurements = estimate_measurements(detection, family, class_rules, geom)
    measure = str(class_rules.get("measure", "area_m2"))
    value = _measure_value(measurements, measure)

    fallback_used = not (measurements["reliable"] and value is not None)
    if not fallback_used:
        size_level, score = _grade(
            value, float(class_rules["moderate"]), float(class_rules["severe"])
        )
        signals_used.append(f"estimated_{measure}")
        if segmentation_available:
            signals_used.append("segmentation_area")
    else:
        # Image-fraction fallback (scale unknown) — always reviewed by a human.
        if segmentation_available and area_pct is not None:
            norm = area_pct / 100.0
            signals_used.append("segmentation_area")
        else:
            norm = geom.get("norm_area") or 0.0
            signals_used.append("bbox_area_fallback")
        size_level, score = _grade(
            norm,
            float(class_rules.get("moderate_bbox_norm_area", 0.03)),
            float(class_rules.get("severe_bbox_norm_area", 0.10)),
        )
        measurements["size_basis"] = "image_fraction_fallback"
        review_reasons.append("size_estimate_unreliable")
    if not segmentation_available:
        unavailable.append("segmentation_area")

    level = size_level
    depth_level = None
    if depth_available and rel_depth is not None:
        signals_used.append("relative_depth")
        severe_d = float(class_rules.get("severe_rel_depth", 0.85))
        depth_level = "SEVERE" if rel_depth >= severe_d else "MINOR"
        if family in {"pothole", "alligator"} and rel_depth >= severe_d and level != "SEVERE":
            level = _bump(level)
            score = max(score, 0.42 if level == "MODERATE" else 0.72)
            signals_used.append("relative_depth_bump")
    else:
        unavailable.append("relative_depth")

    # --- Manual review (confidence lives here, not in severity) ---
    min_review = float(rules.get("confidence_min_review", 0.35))
    ambiguous = float(rules.get("confidence_ambiguous", 0.45))
    signals_used.append("detection_confidence_for_review")
    if confidence < min_review:
        review_reasons.append("low_confidence")
    if level == "SEVERE" and confidence < ambiguous:
        review_reasons.append("severe_with_ambiguous_confidence")
    if size_level == "SEVERE" and confidence < ambiguous:
        review_reasons.append("area_confidence_conflict")
    if depth_level == "SEVERE" and confidence < ambiguous:
        review_reasons.append("relative_depth_confidence_conflict")
    if "too_far_from_camera" in measurements["reasons"]:
        review_reasons.append("far_from_camera")
    if "implausible_size" in measurements["reasons"]:
        review_reasons.append("implausible_size_estimate")
    review_reasons = list(dict.fromkeys(review_reasons))
    manual_review = bool(review_reasons)

    # Depth / volume: assumed by severity band unless measured upstream.
    depth_mm = None
    volume_m3 = None
    if family == "pothole":
        depth_table = class_rules.get("assumed_depth_mm") or {}
        depth_mm = _safe_float(depth_table.get(level))
        if depth_mm is not None and measurements.get("area_m2") is not None:
            volume_m3 = round(measurements["area_m2"] * depth_mm / 1000.0, 5)
    measurements["estimated_depth_mm"] = depth_mm
    measurements["depth_basis"] = "assumed_from_severity_band" if depth_mm is not None else None
    measurements["estimated_volume_m3"] = volume_m3

    priority = infer_priority(level, rules)
    actions = rules.get("actions") or {}
    recommended_action = actions.get(level, "scheduled monitoring")
    repair_table = (rules.get("repair_types") or {}).get(family) or rules["repair_types"]["default"]
    repair_type = repair_table.get(level, repair_table.get("MINOR", "routine monitoring"))

    explanation_parts = [f"class={family}", f"level={level}"]
    if not fallback_used:
        if measure == "diameter_mm":
            explanation_parts.append(
                f"est. diameter {measurements['diameter_mm'] / 1000.0:.2f} m "
                f"(moderate >= {float(class_rules['moderate']) / 1000.0:.2f} m, "
                f"severe >= {float(class_rules['severe']) / 1000.0:.2f} m)"
            )
        elif measure == "length_m":
            explanation_parts.append(
                f"est. crack length {measurements['crack_length_m']:.2f} m "
                f"(moderate >= {class_rules['moderate']} m, severe >= {class_rules['severe']} m)"
            )
        else:
            explanation_parts.append(
                f"est. area {measurements['area_m2']:.2f} m2 "
                f"(moderate >= {class_rules['moderate']} m2, severe >= {class_rules['severe']} m2)"
            )
        explanation_parts.append(
            f"distance ~{measurements['distance_m']} m, assumed camera pose"
        )
    else:
        explanation_parts.append(
            "size estimate unreliable; graded on image fraction "
            f"{(geom.get('norm_area') or 0.0):.4f}"
        )
    explanation_parts.append(f"confidence={confidence:.2f}")
    if depth_available:
        explanation_parts.append(
            f"relative_depth={rel_depth:.3f} (not metric; calibration unavailable)"
        )
    if review_reasons:
        explanation_parts.append("review=" + ",".join(review_reasons))

    return {
        "level": level,
        "priority": priority,
        "score": round(min(1.0, max(0.0, score)), 4),
        "recommended_action": recommended_action,
        "repair_type": repair_type,
        "manual_review_required": manual_review,
        "rule_version": rules.get("rule_version"),
        "threshold_source": rules.get("threshold_source"),
        "damage_family": family,
        "explanation": "; ".join(explanation_parts),
        "measurements": measurements,
        "evidence": {
            "detection": True,
            "segmentation": segmentation_available,
            "relative_depth": depth_available,
            "metric_depth": False,
            "fallback_used": fallback_used,
            "signals_used": signals_used,
            "unavailable": unavailable,
            "confidence": confidence,
            "bbox_width": geom.get("width"),
            "bbox_height": geom.get("height"),
            "bbox_diagonal": geom.get("diagonal"),
            "bbox_norm_area": geom.get("norm_area"),
            "bbox_aspect_ratio": geom.get("aspect_ratio"),
            "mask_pixel_count": mask_pixels,
            "damaged_area_percentage": area_pct,
            "union_damaged_area_percentage": union_damaged_area_percentage,
            "mean_relative_depth": rel_depth,
            "calibration_available": False,
            "physical_volume_available": False,
            "area_unit": "m2_estimate" if not fallback_used else "image_fraction",
            "size_basis": measurements["size_basis"],
            "review_reasons": review_reasons,
            "component_levels": {
                "size": size_level,
                "relative_depth": depth_level,
                "final": level,
            },
        },
    }


def infer_severity(detection: dict[str, Any]) -> str:
    """Backward-compatible level-only API."""
    return assess_severity(detection)["level"]


def build_severity_evidence(detection: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible evidence bag + Phase 10 assessment evidence."""
    assessment = detection.get("severity_assessment") or assess_severity(detection)
    evidence = dict(assessment.get("evidence") or {})
    evidence.update(
        {
            "class_name": detection.get("class_name") or detection.get("damage_type"),
            "severity_level": assessment.get("level"),
            "severity_score": assessment.get("score"),
            "recommended_action": assessment.get("recommended_action"),
            "repair_type": assessment.get("repair_type"),
            "manual_review_required": assessment.get("manual_review_required"),
            "rule_version": assessment.get("rule_version"),
            "explanation": assessment.get("explanation"),
        }
    )
    return evidence


def apply_severity(
    detection: dict[str, Any],
    *,
    rules: dict[str, Any] | None = None,
    union_damaged_area_percentage: float | None = None,
    cost_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach severity, size estimate and repair cost onto a detection dict."""
    from app.ai.cost_engine import estimate_detection_cost

    item = dict(detection)
    assessment = assess_severity(
        item,
        rules=rules,
        union_damaged_area_percentage=union_damaged_area_percentage,
    )
    measurements = assessment.get("measurements") or {}
    cost = estimate_detection_cost(
        assessment["damage_family"],
        assessment["level"],
        measurements,
        rates=cost_rates,
    )
    item["severity"] = assessment["level"]
    item["priority"] = assessment["priority"]
    item["severity_score"] = assessment["score"]
    item["recommended_action"] = assessment["recommended_action"]
    item["repair_type"] = assessment["repair_type"]
    item["manual_review_required"] = assessment["manual_review_required"]
    item["severity_assessment"] = assessment
    item["severity_evidence"] = build_severity_evidence({**item, "severity_assessment": assessment})
    item["measurements"] = measurements
    item["area_sq_meters"] = measurements.get("area_m2")
    item["estimated_depth_mm"] = measurements.get("estimated_depth_mm")
    item["estimated_volume_m3"] = measurements.get("estimated_volume_m3")
    item["cost_estimate"] = cost
    item["estimated_cost"] = cost["amount"]
    if item.get("status") is None:
        item["status"] = "PENDING_REVIEW"
    return item


def enrich_detections(
    detections: list[dict[str, Any]],
    *,
    union_damaged_area_percentage: float | None = None,
) -> list[dict[str, Any]]:
    from app.ai.cost_engine import get_cost_rates

    rules = get_severity_rules()
    rates = get_cost_rates()
    return [
        apply_severity(
            det,
            rules=rules,
            union_damaged_area_percentage=union_damaged_area_percentage,
            cost_rates=rates,
        )
        for det in detections
    ]


def summarize_severities(detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Inspection-level severity rollup for task results."""
    if not detections:
        return {
            "level": None,
            "priority": None,
            "score": None,
            "recommended_action": None,
            "repair_type": None,
            "manual_review_required": False,
            "counts": {"SEVERE": 0, "MODERATE": 0, "MINOR": 0},
            "detection_count": 0,
            "cost": {"total": 0.0, "items_total": 0.0, "item_count": 0, "currency": "INR"},
            "evidence": {
                "detection": False,
                "segmentation": False,
                "relative_depth": False,
                "metric_depth": False,
                "fallback_used": False,
            },
        }

    counts = {"SEVERE": 0, "MODERATE": 0, "MINOR": 0}
    worst = "MINOR"
    worst_priority = 3
    scores: list[float] = []
    any_seg = False
    any_depth = False
    any_fallback = False
    any_review = False
    top: dict[str, Any] | None = None

    for det in detections:
        level = str(det.get("severity") or "MINOR").upper()
        if level not in counts:
            level = "MINOR"
        counts[level] += 1
        assessment = det.get("severity_assessment") or {}
        score = _safe_float(det.get("severity_score"), assessment.get("score") or 0.0) or 0.0
        scores.append(score)
        evidence = assessment.get("evidence") or det.get("severity_evidence") or {}
        any_seg = any_seg or bool(evidence.get("segmentation"))
        any_depth = any_depth or bool(evidence.get("relative_depth"))
        any_fallback = any_fallback or bool(evidence.get("fallback_used"))
        review = bool(det.get("manual_review_required") or assessment.get("manual_review_required"))
        any_review = any_review or review
        if top is None or _level_rank(level) > _level_rank(worst) or (
            level == worst and score > float(top.get("score") or 0)
        ):
            worst = level
            top = assessment or {
                "level": level,
                "priority": det.get("priority"),
                "score": score,
                "recommended_action": det.get("recommended_action"),
                "repair_type": det.get("repair_type"),
            }
        worst_priority = min(worst_priority, int(det.get("priority") or infer_priority(level)))

    top = top or {}
    from app.ai.cost_engine import summarize_costs

    return {
        "level": worst,
        "priority": int(top.get("priority") or worst_priority),
        "score": round(max(scores) if scores else 0.0, 4),
        "recommended_action": top.get("recommended_action")
        or get_severity_rules()["actions"].get(worst),
        "repair_type": top.get("repair_type"),
        "manual_review_required": any_review,
        "counts": counts,
        "detection_count": len(detections),
        "rule_version": get_severity_rules().get("rule_version"),
        "cost": summarize_costs(detections),
        "evidence": {
            "detection": True,
            "segmentation": any_seg,
            "relative_depth": any_depth,
            "metric_depth": False,
            "fallback_used": any_fallback,
        },
    }


__all__ = [
    "assess_severity",
    "apply_severity",
    "infer_severity",
    "infer_priority",
    "enrich_detections",
    "build_severity_evidence",
    "summarize_severities",
    "bbox_geometry",
    "estimate_measurements",
]
