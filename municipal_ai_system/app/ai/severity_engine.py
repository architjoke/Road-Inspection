"""Phase 10 hybrid damage severity engine (configurable; offline-safe)."""

from __future__ import annotations

import logging
from typing import Any

from app.ai.severity_rules import (
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


def _level_from_score(score: float, rules: dict[str, Any]) -> str:
    thr = rules.get("score_thresholds") or {}
    if score >= float(thr.get("severe", 0.72)):
        return "SEVERE"
    if score >= float(thr.get("moderate", 0.42)):
        return "MODERATE"
    return "MINOR"


def _level_rank(level: str) -> int:
    return {"MINOR": 0, "MODERATE": 1, "SEVERE": 2}.get(level.upper(), 0)


def _max_level(*levels: str) -> str:
    best = "MINOR"
    for level in levels:
        if _level_rank(level) > _level_rank(best):
            best = level.upper()
    return best


def assess_severity(
    detection: dict[str, Any],
    *,
    rules: dict[str, Any] | None = None,
    union_damaged_area_percentage: float | None = None,
) -> dict[str, Any]:
    """
    Hybrid severity assessment for one detection.

    Detection is baseline; segmentation improves area; relative depth supports only.
    Never claims metric depth/volume without calibration.
    """
    rules = rules or get_severity_rules()
    family = classify_damage_family(
        detection.get("class_name"), detection.get("damage_type")
    )
    class_rules = (rules.get("classes") or {}).get(family) or rules["classes"]["default"]
    weights = rules.get("weights") or {}

    confidence = _safe_float(detection.get("confidence"), 0.0) or 0.0
    geom = bbox_geometry(detection)
    area_pct = _safe_float(detection.get("damaged_area_percentage"))
    mask_pixels = detection.get("mask_pixel_count")
    rel_depth = _safe_float(detection.get("mean_relative_depth"))
    metric_depth = bool(detection.get("metric_depth_available")) and bool(
        detection.get("calibration_available")
    )

    segmentation_available = area_pct is not None or mask_pixels is not None
    depth_available = rel_depth is not None
    fallback_used = not segmentation_available

    signals_used: list[str] = ["detection_confidence", "damage_class"]
    unavailable: list[str] = []
    if segmentation_available:
        signals_used.append("segmentation_area")
    else:
        unavailable.append("segmentation_area")
        signals_used.append("bbox_area_fallback")
    if depth_available:
        signals_used.append("relative_depth")
    else:
        unavailable.append("relative_depth")
    unavailable.append("metric_depth")
    unavailable.append("physical_volume")

    # --- component scores in [0, 1] ---
    conf_score = min(1.0, max(0.0, confidence))

    if segmentation_available and area_pct is not None:
        severe_a = float(class_rules.get("severe_area_pct", 5.0))
        moderate_a = float(class_rules.get("moderate_area_pct", 1.5))
        if area_pct >= severe_a:
            area_score = 1.0
            area_level = "SEVERE"
        elif area_pct >= moderate_a:
            area_score = 0.65
            area_level = "MODERATE"
        else:
            area_score = min(0.45, area_pct / max(moderate_a, 1e-6) * 0.45)
            area_level = "MINOR"
        seg_weight = float(weights.get("segmentation_area", 0.30))
        bbox_weight = 0.0
        bbox_score = 0.0
        bbox_level = "MINOR"
    else:
        norm = geom.get("norm_area") or 0.0
        severe_b = float(class_rules.get("severe_bbox_norm_area", 0.08))
        moderate_b = float(class_rules.get("moderate_bbox_norm_area", 0.03))
        if norm >= severe_b:
            bbox_score = 1.0
            bbox_level = "SEVERE"
        elif norm >= moderate_b:
            bbox_score = 0.65
            bbox_level = "MODERATE"
        else:
            bbox_score = min(0.45, (norm / max(moderate_b, 1e-9)) * 0.45)
            bbox_level = "MINOR"
        area_score = 0.0
        area_level = "MINOR"
        seg_weight = 0.0
        bbox_weight = float(weights.get("bbox_area", 0.25)) + float(
            weights.get("segmentation_area", 0.30)
        )

    depth_score = 0.0
    depth_level = "MINOR"
    depth_weight = float(weights.get("relative_depth", 0.10))
    if depth_available and rel_depth is not None:
        severe_d = float(class_rules.get("severe_rel_depth", 0.75))
        moderate_d = float(class_rules.get("moderate_rel_depth", 0.55))
        if rel_depth >= severe_d:
            depth_score = 1.0
            depth_level = "SEVERE"
        elif rel_depth >= moderate_d:
            depth_score = 0.6
            depth_level = "MODERATE"
        else:
            depth_score = rel_depth * 0.5
            depth_level = "MINOR"
    else:
        depth_weight = 0.0

    # Crack elongation can raise severity when very thin long boxes
    elongation_boost = 0.0
    aspect = geom.get("aspect_ratio")
    elong_thr = class_rules.get("elongation_severe")
    if aspect is not None and elong_thr is not None and float(aspect) >= float(elong_thr):
        elongation_boost = 0.08
        signals_used.append("bbox_elongation")

    conf_weight = float(weights.get("confidence", 0.35))
    # Renormalize active weights
    active = conf_weight + bbox_weight + seg_weight + depth_weight
    if active <= 0:
        active = 1.0
    score = (
        conf_weight * conf_score
        + bbox_weight * bbox_score
        + seg_weight * area_score
        + depth_weight * depth_score
    ) / active
    score = min(1.0, score + elongation_boost)

    score_level = _level_from_score(score, rules)

    # Class confidence floor rules (legacy-compatible baseline)
    severe_c = float(class_rules.get("severe_confidence", 0.7))
    moderate_c = float(class_rules.get("moderate_confidence", 0.45))
    if confidence >= severe_c and family in {"pothole", "alligator"}:
        conf_level = "SEVERE"
    elif confidence >= moderate_c:
        conf_level = "MODERATE" if family not in {"pothole"} or confidence < severe_c else "SEVERE"
    else:
        conf_level = "MINOR"
    # Preserve prior pothole behavior: moderate/high conf tends severe
    if family == "pothole" and confidence >= severe_c:
        conf_level = "SEVERE"
    elif family == "alligator" and confidence >= severe_c:
        conf_level = "SEVERE"
    elif family in {"longitudinal", "transverse", "crack"} and confidence >= severe_c:
        conf_level = "MODERATE"
    elif confidence >= moderate_c:
        conf_level = "MODERATE" if family != "pothole" else "MODERATE"
    else:
        conf_level = "MINOR"

    evidence_level = _max_level(
        score_level,
        conf_level,
        area_level if segmentation_available else bbox_level,
        depth_level if depth_available else "MINOR",
    )

    # Manual review conditions
    manual_review = False
    review_reasons: list[str] = []
    min_review = float(rules.get("confidence_min_review", 0.35))
    ambiguous = float(rules.get("confidence_ambiguous", 0.45))
    if confidence < min_review:
        manual_review = True
        review_reasons.append("low_confidence")
    if confidence < ambiguous and evidence_level == "SEVERE":
        manual_review = True
        review_reasons.append("severe_with_ambiguous_confidence")

    # Conflicting evidence: high area/depth vs low confidence or opposite extremes
    high_area = (segmentation_available and area_level == "SEVERE") or (
        fallback_used and bbox_level == "SEVERE"
    )
    high_depth = depth_available and depth_level == "SEVERE"
    if high_area and confidence < ambiguous:
        manual_review = True
        review_reasons.append("area_confidence_conflict")
    if high_depth and confidence < ambiguous:
        manual_review = True
        review_reasons.append("relative_depth_confidence_conflict")
    if high_area and depth_available and depth_level == "MINOR" and area_level == "SEVERE":
        manual_review = True
        review_reasons.append("area_depth_conflict")

    if manual_review and evidence_level == "SEVERE" and confidence < min_review:
        # Soften absolute SEVERE claims on very low confidence
        evidence_level = "MODERATE"
        review_reasons.append("downgraded_severe_for_low_confidence")

    level = evidence_level
    priority = infer_priority(level, rules)
    actions = rules.get("actions") or {}
    recommended_action = actions.get(level, "scheduled monitoring")

    repair_table = (rules.get("repair_types") or {}).get(family) or rules["repair_types"]["default"]
    repair_type = repair_table.get(level, repair_table.get("MINOR", "routine monitoring"))

    explanation_parts = [
        f"class={family}",
        f"confidence={confidence:.2f}",
        f"hybrid_score={score:.2f}",
        f"level={level}",
    ]
    if segmentation_available:
        explanation_parts.append(f"seg_area_pct={area_pct}")
    else:
        explanation_parts.append(f"bbox_norm_area={geom.get('norm_area')}")
    if depth_available:
        explanation_parts.append(
            f"relative_depth={rel_depth:.3f} (not metric; calibration unavailable)"
        )
    if review_reasons:
        explanation_parts.append("review=" + ",".join(review_reasons))

    assessment = {
        "level": level,
        "priority": priority,
        "score": round(score, 4),
        "recommended_action": recommended_action,
        "repair_type": repair_type,
        "manual_review_required": manual_review,
        "rule_version": rules.get("rule_version"),
        "damage_family": family,
        "explanation": "; ".join(explanation_parts),
        "evidence": {
            "detection": True,
            "segmentation": segmentation_available,
            "relative_depth": depth_available,
            "metric_depth": False if not metric_depth else True,
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
            "area_unit": "pixels" if segmentation_available else "normalized_bbox",
            "review_reasons": review_reasons,
            "component_levels": {
                "confidence": conf_level,
                "area": area_level if segmentation_available else bbox_level,
                "relative_depth": depth_level if depth_available else None,
                "hybrid_score": score_level,
            },
        },
    }
    return assessment


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
) -> dict[str, Any]:
    """Attach severity fields onto a detection dict (additive)."""
    item = dict(detection)
    assessment = assess_severity(
        item,
        rules=rules,
        union_damaged_area_percentage=union_damaged_area_percentage,
    )
    item["severity"] = assessment["level"]
    item["priority"] = assessment["priority"]
    item["severity_score"] = assessment["score"]
    item["recommended_action"] = assessment["recommended_action"]
    item["repair_type"] = assessment["repair_type"]
    item["manual_review_required"] = assessment["manual_review_required"]
    item["severity_assessment"] = assessment
    item["severity_evidence"] = build_severity_evidence({**item, "severity_assessment": assessment})
    if item.get("status") is None:
        item["status"] = "PENDING_REVIEW"
    return item


def enrich_detections(
    detections: list[dict[str, Any]],
    *,
    union_damaged_area_percentage: float | None = None,
) -> list[dict[str, Any]]:
    rules = get_severity_rules()
    return [
        apply_severity(
            det,
            rules=rules,
            union_damaged_area_percentage=union_damaged_area_percentage,
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
        evidence = (assessment.get("evidence") or det.get("severity_evidence") or {})
        any_seg = any_seg or bool(evidence.get("segmentation"))
        any_depth = any_depth or bool(evidence.get("relative_depth"))
        any_fallback = any_fallback or bool(evidence.get("fallback_used"))
        review = bool(
            det.get("manual_review_required")
            or assessment.get("manual_review_required")
        )
        any_review = any_review or review
        if _level_rank(level) > _level_rank(worst) or (
            level == worst and top is not None and score > float(top.get("score") or 0)
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
        "evidence": {
            "detection": True,
            "segmentation": any_seg,
            "relative_depth": any_depth,
            "metric_depth": False,
            "fallback_used": any_fallback,
        },
    }


# Compatibility: attach helpers live in detection_postprocessor to avoid cycles.


__all__ = [
    "assess_severity",
    "apply_severity",
    "infer_severity",
    "infer_priority",
    "enrich_detections",
    "build_severity_evidence",
    "summarize_severities",
    "bbox_geometry",
]
