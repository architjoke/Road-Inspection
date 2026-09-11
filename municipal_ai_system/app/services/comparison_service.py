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
    verified = SSIM_VERIFIED_MAX
    partial = SSIM_PARTIAL_MAX
    try:
        verified = float(
            current_app.config.get("VERIFICATION_SSIM_VERIFIED_MAX", verified)
        )
        partial = float(
            current_app.config.get("VERIFICATION_SSIM_PARTIAL_MAX", partial)
        )
    except Exception:
        pass
    import os

    if os.getenv("VERIFICATION_SSIM_VERIFIED_MAX"):
        try:
            verified = float(os.getenv("VERIFICATION_SSIM_VERIFIED_MAX"))
        except ValueError:
            pass
    if os.getenv("VERIFICATION_SSIM_PARTIAL_MAX"):
        try:
            partial = float(os.getenv("VERIFICATION_SSIM_PARTIAL_MAX"))
        except ValueError:
            pass
    return verified, partial


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
    )

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
