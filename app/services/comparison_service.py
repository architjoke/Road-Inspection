"""Before/after verification persistence (in-memory offline; optional reflected DB)."""

from __future__ import annotations

import logging
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app

from app.ai.before_after_verifier import (
    RULE_VERSION,
    SSIM_PARTIAL_MAX,
    SSIM_VERIFIED_MAX,
    STATUS_NO_CHANGE,
    STATUS_PARTIAL,
    STATUS_VERIFIED,
    aggregate_completion,
    compare_before_after,
)

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_comparisons: dict[str, dict[str, Any]] = {}


def clear_comparisons() -> None:
    with _lock:
        _comparisons.clear()


def list_comparisons(inspection_id: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_comparisons.values())
    if inspection_id is not None:
        rows = [r for r in rows if r.get("inspection_id") == inspection_id]
    return deepcopy(rows)


def get_comparison(comparison_id: str) -> dict[str, Any] | None:
    with _lock:
        item = _comparisons.get(comparison_id)
        return deepcopy(item) if item else None


def _thresholds_from_config() -> tuple[float, float]:
    """Explicit env var > system_settings (DB) > Flask config > code default."""
    import os

    from app.ai.severity_rules import load_db_setting

    def pick(env_key: str, db_key: str, default: float) -> float:
        raw = os.getenv(env_key)
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        db_value = load_db_setting(db_key)
        if db_value is not None:
            try:
                return float(db_value)
            except (TypeError, ValueError):
                pass
        try:
            return float(current_app.config.get(env_key, default))
        except Exception:
            return default

    verified = pick(
        "VERIFICATION_SSIM_VERIFIED_MAX", "ssim_repair_verified_threshold", SSIM_VERIFIED_MAX
    )
    partial = pick(
        "VERIFICATION_SSIM_PARTIAL_MAX", "ssim_manual_review_threshold", SSIM_PARTIAL_MAX
    )
    return verified, partial


# After photo must be taken within this distance of the inspection (if it has GPS).
AFTER_PHOTO_MAX_DISTANCE_M = 30.0


def _after_photo_gps_check(after_path, inspection_latlng) -> tuple[float | None, str | None]:
    if not inspection_latlng or None in inspection_latlng:
        return None, None
    from app.services.gps_service import extract_gps_from_image, haversine_m

    gps = extract_gps_from_image(after_path)
    if not gps:
        return None, None
    distance = haversine_m(
        float(inspection_latlng[0]), float(inspection_latlng[1]), gps["latitude"], gps["longitude"]
    )
    if distance > AFTER_PHOTO_MAX_DISTANCE_M:
        return distance, f"after photo was taken {distance:.0f} m from the inspection location"
    return distance, None


def make_damage_check(damage_family: str | None, after_path: str | Path | None = None):
    """Detector re-check: does the model still see damage inside the repaired box?

    The box test runs on the aligned (warped) after photo so coordinates match the
    before photo. Warping resamples pixels and leaves fill at the edges, which can
    produce faint false detections, so damage only counts when the model also finds
    that damage type in the original after photo.
    """

    def _family_matches(det) -> bool:
        from app.ai.severity_rules import classify_damage_family

        return not damage_family or classify_damage_family(det.get("class_name")) == damage_family

    def check(aligned_path: str, roi) -> bool:
        from app.ai.yolo_inference import detect_image

        if after_path is not None and not any(_family_matches(d) for d in detect_image(after_path)):
            return False
        x1, y1, x2, y2 = roi
        for det in detect_image(aligned_path):
            if not _family_matches(det):
                continue
            bx1, by1, bx2, by2 = det["bbox_xyxy"]
            iw = max(0.0, min(x2, bx2) - max(x1, bx1))
            ih = max(0.0, min(y2, by2) - max(y1, by1))
            inter = iw * ih
            union = (x2 - x1) * (y2 - y1) + (bx2 - bx1) * (by2 - by1) - inter
            if union > 0 and inter / union >= 0.1:
                return True
        return False

    return check


def _output_dir(comparison_id: str) -> Path:
    root = Path(current_app.config.get("TEMP_UPLOAD_ROOT", Path("tmp")))
    out = root / "verifications" / comparison_id
    out.mkdir(parents=True, exist_ok=True)
    return out


# Severity of a per-area status when several damage areas are combined (worst wins).
_STATUS_RANK = {STATUS_VERIFIED: 0, STATUS_PARTIAL: 1, STATUS_NO_CHANGE: 2}
MAX_AUTO_AREAS = 6


def _detect_damage_areas(before_path) -> list[dict[str, Any]]:
    """Damage boxes found by the model in the before photo (strongest first)."""
    from app.ai.yolo_inference import detect_image

    try:
        dets = detect_image(before_path)
    except Exception as exc:  # no model / unreadable image: fall back to whole photo
        logger.warning("Damage detection on the before photo failed: %s", exc)
        return []
    dets.sort(key=lambda d: float(d.get("confidence") or 0), reverse=True)
    return dets[:MAX_AUTO_AREAS]


def _review_min_confidence() -> float:
    try:
        from app.ai.severity_rules import get_severity_rules

        return float(get_severity_rules().get("confidence_min_review", 0.35))
    except Exception:
        return 0.35


def _compare_detected_areas(before_path, after_path, *, out_dir, verified_max, partial_max):
    """
    Verify every damage area the model finds in the before photo.

    Used when no inspection / repair supplies the damage box (photos uploaded
    straight into Verify SSIM). Confident detections must all be repaired;
    low-confidence ones are reported but do not block. Returns None when the
    model finds nothing, so the caller falls back to a whole-photo comparison.
    """
    areas = _detect_damage_areas(before_path)
    if not areas:
        return None
    threshold = _review_min_confidence()
    blocking = [a for a in areas if float(a.get("confidence") or 0) >= threshold] or areas[:1]
    results = []
    for index, area in enumerate(areas):
        res = compare_before_after(
            before_path,
            after_path,
            output_dir=out_dir if index == 0 else None,
            verified_max=verified_max,
            partial_max=partial_max,
            roi=tuple(int(v) for v in area["bbox_xyxy"]),
            damage_check=make_damage_check(None, after_path),
        )
        results.append((area, res))

    def label(area):
        name = str(area.get("damage_type") or area.get("class_name") or "damage").replace("_", " ")
        return f"{name} {float(area.get('confidence') or 0):.0%}"

    def clear(res):
        return res["status"] == STATUS_VERIFIED and not res["manual_review_required"]

    blocking_results = [(a, r) for a, r in results if any(a is b for b in blocking)]
    combined = dict(blocking_results[0][1])
    worst = max(blocking_results, key=lambda ar: _STATUS_RANK.get(ar[1]["status"], 3))
    combined["status"] = STATUS_VERIFIED if all(clear(r) for _, r in blocking_results) else worst[1]["status"]
    combined["manual_review_required"] = not all(clear(r) for _, r in blocking_results)
    combined["review_reasons"] = [
        f"{label(a)}: {reason}" for a, r in blocking_results for reason in r["review_reasons"]
    ]
    if combined["manual_review_required"] and not combined["review_reasons"]:
        combined["review_reasons"] = [f"{label(worst[0])}: {worst[1]['status']}"]
    notes = [f"damage areas found by AI in the before photo: {len(areas)} "
             f"({len(blocking_results)} checked as required)"]
    for area, res in results:
        if any(area is b for b in blocking):
            continue
        state = "repaired" if clear(res) else res["status"].split(" -")[0].lower()
        notes.append(f"lower-confidence area {label(area)}: {state}")
    combined["notes"] = list(dict.fromkeys(notes + [n for _, r in results for n in r.get("notes") or []]))
    roi_values = [r["roi_ssim"] for _, r in blocking_results if r.get("roi_ssim") is not None]
    if roi_values:
        combined["roi_ssim"] = combined["ssim"] = round(sum(roi_values) / len(roi_values), 6)
    combined["completion_percentage"] = min(r["completion_percentage"] for _, r in blocking_results)
    combined["roi"] = [int(v) for v in blocking[0]["bbox_xyxy"]]
    combined["damage_areas"] = [
        {
            "label": label(a),
            "bbox": [int(v) for v in a["bbox_xyxy"]],
            "confidence": round(float(a.get("confidence") or 0), 4),
            "required": any(a is b for b in blocking),
            "status": r["status"],
            "roi_ssim": r.get("roi_ssim"),
            "manual_review_required": r["manual_review_required"],
        }
        for a, r in results
    ]
    return combined


def run_verification(
    before_path: str | Path,
    after_path: str | Path,
    *,
    inspection_id: int | None = None,
    before_media_id: int | None = None,
    after_media_id: int | None = None,
    roi: list[int] | None = None,
    save_artifacts: bool = True,
    damage_family: str | None = None,
    inspection_latlng: tuple[float, float] | None = None,
    detection_id: int | None = None,
) -> dict[str, Any]:
    """Compare before/after images and persist metadata in memory."""
    comparison_id = str(uuid.uuid4())
    verified_max, partial_max = _thresholds_from_config()
    out_dir = _output_dir(comparison_id) if save_artifacts else None

    result = None
    if not (roi and len(roi) == 4):
        # No damage box from an inspection/repair: let the model find the damage.
        result = _compare_detected_areas(
            before_path, after_path, out_dir=out_dir,
            verified_max=verified_max, partial_max=partial_max,
        )
    if result is None:
        result = compare_before_after(
            before_path,
            after_path,
            output_dir=out_dir,
            verified_max=verified_max,
            partial_max=partial_max,
            roi=tuple(roi) if roi and len(roi) == 4 else None,
            damage_check=make_damage_check(damage_family, after_path) if roi else None,
        )
    distance, gps_reason = _after_photo_gps_check(after_path, inspection_latlng)
    if gps_reason:
        result["review_reasons"].append(gps_reason)
        result["manual_review_required"] = True

    record = {
        "comparison_id": comparison_id,
        "inspection_id": inspection_id,
        "before_media_id": before_media_id,
        "after_media_id": after_media_id,
        "ssim": result["ssim"],
        "mean_absolute_diff": result["mean_absolute_diff"],
        "status": result["status"],
        "completion_percentage": result["completion_percentage"],
        "manual_review_required": result["manual_review_required"],
        "review_reasons": result.get("review_reasons") or [],
        "notes": result.get("notes") or [],
        "damage_areas": result.get("damage_areas"),
        "roi": result.get("roi"),
        "detection_id": detection_id,
        "roi_ssim": result.get("roi_ssim"),
        "context_ssim": result.get("context_ssim"),
        "roi_change_vs_context": result.get("roi_change_vs_context"),
        "whole_frame_ssim": result.get("whole_frame_ssim"),
        "overlap_ratio": result.get("overlap_ratio"),
        "after_photo_distance_m": round(distance, 1) if distance is not None else None,
        "alignment_success": result["alignment"]["success"],
        "alignment_method": result["alignment"]["method"],
        "homography_success": result["alignment"]["homography_available"]
        and result["alignment"]["success"],
        "match_count": result["alignment"]["match_count"],
        "inlier_ratio": result["alignment"]["inlier_ratio"],
        "aligned_after_path": result.get("aligned_after_path"),
        "diff_path": result.get("diff_path"),
        "before_path": result.get("before_path"),
        "after_path": result.get("after_path"),
        "thresholds": result["thresholds"],
        "rule_version": RULE_VERSION,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "metric_notes": result.get("metric_notes"),
    }

    with _lock:
        _comparisons[comparison_id] = record

    return deepcopy(record)


def summarize_inspection_verifications(inspection_id: int) -> dict[str, Any]:
    rows = list_comparisons(inspection_id=inspection_id)
    summary = aggregate_completion(rows)
    summary["inspection_id"] = inspection_id
    summary["comparisons"] = rows
    return summary
