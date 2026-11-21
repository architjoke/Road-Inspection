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


def make_damage_check(damage_family: str | None):
    """Detector re-check: does the model still see damage inside the repaired box?"""

    def check(aligned_path: str, roi) -> bool:
        from app.ai.severity_rules import classify_damage_family
        from app.ai.yolo_inference import detect_image

        x1, y1, x2, y2 = roi
        for det in detect_image(aligned_path):
            if damage_family and classify_damage_family(det.get("class_name")) != damage_family:
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

    result = compare_before_after(
        before_path,
        after_path,
        output_dir=out_dir,
        verified_max=verified_max,
        partial_max=partial_max,
        roi=tuple(roi) if roi and len(roi) == 4 else None,
        damage_check=make_damage_check(damage_family) if roi else None,
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
