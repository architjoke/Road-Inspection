"""Phase 12 before/after image alignment and SSIM change detection (OpenCV)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Decision thresholds (configurable via compare() kwargs / Flask config)
SSIM_VERIFIED_MAX = 0.60  # below → significant change → repair verified
SSIM_PARTIAL_MAX = 0.85  # between verified and this → partial
# above SSIM_PARTIAL_MAX → no change

STATUS_VERIFIED = "Repair Verified"
STATUS_PARTIAL = "Partial Repair - Manual Review Required"
STATUS_NO_CHANGE = "No Change Detected - Manual Review Required"
STATUS_ALIGNMENT_FAILED = "Alignment Uncertain - Manual Review Required"

RULE_VERSION = "phase12-v1"


class VerificationError(Exception):
    """Controlled verification failure."""


@dataclass
class AlignmentResult:
    aligned_after: np.ndarray
    homography: np.ndarray | None
    method: str  # "orb_homography" | "resize_fallback"
    inlier_ratio: float
    match_count: int
    success: bool


def _read_bgr(path: str | Path) -> np.ndarray:
    path = Path(path)
    if not path.is_file():
        raise VerificationError(f"Image not found: {path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise VerificationError(f"Could not decode image: {path}")
    return image


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def compute_ssim(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """
    Structural Similarity Index on grayscale images (OpenCV + NumPy).

    Returns score in approximately [-1, 1], typically [0, 1] for natural images.
    """
    a = _to_gray(img_a).astype(np.float64)
    b = _to_gray(img_b).astype(np.float64)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    mu1 = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(b, (11, 11), 1.5)
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu1_mu2

    numerator = (2 * mu1_mu2 + c1) * (2 * sigma12 + c2)
    denominator = (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    ssim_map = numerator / (denominator + 1e-12)
    score = float(np.mean(ssim_map))
    return max(-1.0, min(1.0, score))


def align_images(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    *,
    max_features: int = 2000,
    min_matches: int = 12,
    ransac_reproj: float = 5.0,
) -> AlignmentResult:
    """
    Align `after` into `before` coordinate space via ORB + homography (RANSAC).

    Falls back to resize if matching fails.
    """
    h, w = before_bgr.shape[:2]
    before_gray = _to_gray(before_bgr)
    after_gray = _to_gray(after_bgr)

    orb = cv2.ORB_create(nfeatures=max_features)
    kp1, des1 = orb.detectAndCompute(before_gray, None)
    kp2, des2 = orb.detectAndCompute(after_gray, None)

    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        resized = cv2.resize(after_bgr, (w, h), interpolation=cv2.INTER_AREA)
        return AlignmentResult(
            aligned_after=resized,
            homography=None,
            method="resize_fallback",
            inlier_ratio=0.0,
            match_count=0,
            success=False,
        )

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des1, des2, k=2)
    good = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)

    if len(good) < min_matches:
        resized = cv2.resize(after_bgr, (w, h), interpolation=cv2.INTER_AREA)
        return AlignmentResult(
            aligned_after=resized,
            homography=None,
            method="resize_fallback",
            inlier_ratio=0.0,
            match_count=len(good),
            success=False,
        )

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    # Map after → before: use dst (after) as source for warpPerspective into before frame
    homography, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, ransac_reproj)
    if homography is None:
        resized = cv2.resize(after_bgr, (w, h), interpolation=cv2.INTER_AREA)
        return AlignmentResult(
            aligned_after=resized,
            homography=None,
            method="resize_fallback",
            inlier_ratio=0.0,
            match_count=len(good),
            success=False,
        )

    inliers = int(mask.sum()) if mask is not None else 0
    inlier_ratio = float(inliers) / float(len(good)) if good else 0.0
    warped = cv2.warpPerspective(after_bgr, homography, (w, h))
    return AlignmentResult(
        aligned_after=warped,
        homography=homography,
        method="orb_homography",
        inlier_ratio=round(inlier_ratio, 4),
        match_count=len(good),
        success=inlier_ratio >= 0.15 and inliers >= min_matches // 2,
    )


def classify_ssim(
    ssim: float,
    *,
    verified_max: float = SSIM_VERIFIED_MAX,
    partial_max: float = SSIM_PARTIAL_MAX,
    alignment_success: bool = True,
) -> dict[str, Any]:
    """Map SSIM to verification status + manual-review flag."""
    manual_review = False
    if not alignment_success:
        # Still classify by SSIM but force review when alignment fell back
        manual_review = True

    if ssim < verified_max:
        status = STATUS_VERIFIED
        completion_pct = round(min(100.0, (verified_max - ssim) / verified_max * 100.0 + 70.0), 2)
        completion_pct = max(70.0, min(100.0, completion_pct))
    elif ssim <= partial_max:
        status = STATUS_PARTIAL
        manual_review = True
        # Linear map 0.60→65% … 0.85→35%
        span = max(1e-6, partial_max - verified_max)
        completion_pct = round(65.0 - ((ssim - verified_max) / span) * 30.0, 2)
    else:
        status = STATUS_NO_CHANGE
        manual_review = True
        completion_pct = round(max(0.0, 30.0 - (ssim - partial_max) * 100.0), 2)

    if not alignment_success and status == STATUS_VERIFIED:
        status = STATUS_ALIGNMENT_FAILED
        manual_review = True

    # Boundary band near thresholds → review
    if abs(ssim - verified_max) < 0.03 or abs(ssim - partial_max) < 0.03:
        manual_review = True

    return {
        "status": status,
        "manual_review_required": manual_review,
        "completion_percentage": max(0.0, min(100.0, completion_pct)),
        "thresholds": {
            "verified_max_ssim": verified_max,
            "partial_max_ssim": partial_max,
        },
    }


def mean_absolute_diff(img_a: np.ndarray, img_b: np.ndarray) -> float:
    a = _to_gray(img_a).astype(np.float32)
    b = _to_gray(img_b).astype(np.float32)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.mean(np.abs(a - b)) / 255.0)


def compare_before_after(
    before_path: str | Path,
    after_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    verified_max: float = SSIM_VERIFIED_MAX,
    partial_max: float = SSIM_PARTIAL_MAX,
    roi: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """
    Full verification pipeline: align → SSIM → classify.

    Never downloads models. Uses OpenCV ORB only.
    """
    before = _read_bgr(before_path)
    after = _read_bgr(after_path)
    alignment = align_images(before, after)
    aligned = alignment.aligned_after

    if roi is not None:
        x1, y1, x2, y2 = roi
        h, w = before.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            before_roi = before[y1:y2, x1:x2]
            aligned_roi = aligned[y1:y2, x1:x2]
        else:
            before_roi, aligned_roi = before, aligned
            roi = None
    else:
        before_roi, aligned_roi = before, aligned

    ssim = compute_ssim(before_roi, aligned_roi)
    mad = mean_absolute_diff(before_roi, aligned_roi)
    decision = classify_ssim(
        ssim,
        verified_max=verified_max,
        partial_max=partial_max,
        alignment_success=alignment.success,
    )

    aligned_path = None
    diff_path = None
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        aligned_path = str(out / "after_aligned.jpg")
        diff_path = str(out / "absdiff.jpg")
        cv2.imwrite(aligned_path, aligned)
        gray_b = _to_gray(before)
        gray_a = _to_gray(aligned)
        diff = cv2.absdiff(gray_b, gray_a)
        cv2.imwrite(diff_path, diff)

    return {
        "ssim": round(float(ssim), 6),
        "mean_absolute_diff": round(float(mad), 6),
        "status": decision["status"],
        "completion_percentage": decision["completion_percentage"],
        "manual_review_required": decision["manual_review_required"],
        "alignment": {
            "method": alignment.method,
            "success": alignment.success,
            "match_count": alignment.match_count,
            "inlier_ratio": alignment.inlier_ratio,
            "homography_available": alignment.homography is not None,
        },
        "thresholds": decision["thresholds"],
        "roi": list(roi) if roi else None,
        "aligned_after_path": aligned_path,
        "diff_path": diff_path,
        "before_path": str(before_path),
        "after_path": str(after_path),
        "rule_version": RULE_VERSION,
        "metric_notes": (
            "SSIM measures structural similarity (unitless). "
            "Lower SSIM ⇒ more visual change after alignment."
        ),
    }


def aggregate_completion(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate multi-damage completion for an inspection ticket."""
    if not results:
        return {
            "completion_percentage": 0.0,
            "status": STATUS_NO_CHANGE,
            "manual_review_required": True,
            "count": 0,
        }
    pcts = [float(r.get("completion_percentage") or 0.0) for r in results]
    mean_pct = round(sum(pcts) / len(pcts), 2)
    any_review = any(r.get("manual_review_required") for r in results)
    statuses = [r.get("status") for r in results]
    if all(s == STATUS_VERIFIED for s in statuses):
        status = STATUS_VERIFIED
    elif any(s == STATUS_VERIFIED for s in statuses) or any(
        s == STATUS_PARTIAL for s in statuses
    ):
        status = STATUS_PARTIAL
        any_review = True
    else:
        status = STATUS_NO_CHANGE
        any_review = True
    return {
        "completion_percentage": mean_pct,
        "status": status,
        "manual_review_required": any_review,
        "count": len(results),
    }
