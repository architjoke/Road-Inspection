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
STATUS_SCENE_MISMATCH = "Different Road Images - Comparison Rejected"

RULE_VERSION = "phase12-v3-roi"

# Phone photos are 12+ MP; SSIM/ORB at full size is slow and memory-hungry on a PC
# and adds no accuracy for a pothole-sized region.
MAX_SIDE_PX = 1280
# Pixels of the before frame that the warped after photo must cover.
MIN_OVERLAP = 0.6
# Damage region must change this much more than its surroundings (SSIM points)
# before a repair can be auto-verified; lighting/time-of-day changes hit both.
MIN_ROI_CONTEXT_DROP = 0.15
PARTIAL_ROI_CONTEXT_DROP = 0.08

# Same-scene gate: different roads usually fail ORB matching hard.
MIN_SAME_SCENE_MATCHES = 12
MIN_SAME_SCENE_INLIER_RATIO = 0.12
MIN_HISTOGRAM_CORR = 0.35


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
    valid_mask: np.ndarray | None = None  # pixels the after photo actually covers


def _read_bgr(path: str | Path, max_side: int | None = None) -> tuple[np.ndarray, float]:
    """Read (EXIF-upright, as OpenCV does) and downscale; returns (image, scale)."""
    path = Path(path)
    if not path.is_file():
        raise VerificationError(f"Image not found: {path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise VerificationError(f"Could not decode image: {path}")
    scale = 1.0
    if max_side:
        h, w = image.shape[:2]
        longest = max(h, w)
        if longest > max_side:
            scale = max_side / float(longest)
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return image, scale


def _normalize_lighting(gray: np.ndarray) -> np.ndarray:
    """CLAHE so exposure / time-of-day differences do not read as change."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _ssim_map(img_a: np.ndarray, img_b: np.ndarray) -> np.ndarray:
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
    return numerator / (denominator + 1e-12)


def compute_ssim(
    img_a: np.ndarray,
    img_b: np.ndarray,
    mask: np.ndarray | None = None,
) -> float:
    """
    Structural Similarity Index on grayscale images (OpenCV + NumPy).

    ``mask`` (bool/0-1, same HxW) limits the average to pixels both photos
    really cover, so black warp borders cannot fake a big change.
    Returns score in approximately [-1, 1], typically [0, 1] for natural images.
    """
    ssim_map = _ssim_map(img_a, img_b)
    if mask is not None:
        valid = mask.astype(bool)
        if valid.shape != ssim_map.shape:
            valid = cv2.resize(
                valid.astype(np.uint8), (ssim_map.shape[1], ssim_map.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        if valid.sum() < 64:
            return float("nan")
        score = float(np.mean(ssim_map[valid]))
    else:
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
    before_gray = _normalize_lighting(_to_gray(before_bgr))
    after_gray = _normalize_lighting(_to_gray(after_bgr))

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
    ah, aw = after_bgr.shape[:2]
    coverage = cv2.warpPerspective(
        np.full((ah, aw), 255, np.uint8), homography, (w, h), flags=cv2.INTER_NEAREST
    )
    # Shrink by the SSIM window so border pixels never mix with black fill.
    coverage = cv2.erode(coverage, np.ones((11, 11), np.uint8))
    return AlignmentResult(
        aligned_after=warped,
        homography=homography,
        method="orb_homography",
        inlier_ratio=round(inlier_ratio, 4),
        match_count=len(good),
        success=inlier_ratio >= 0.15 and inliers >= min_matches // 2,
        valid_mask=coverage > 0,
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


def _histogram_correlation(img_a: np.ndarray, img_b: np.ndarray) -> float:
    a = _to_gray(img_a)
    b = _to_gray(img_b)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    hist_a = cv2.calcHist([a], [0], None, [64], [0, 256])
    hist_b = cv2.calcHist([b], [0], None, [64], [0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    corr = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL)
    return float(corr)


def assert_same_road_scene(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    alignment: AlignmentResult,
) -> None:
    """
    Reject clearly different road/site photos.

    Same location with repair work should still share enough ORB features /
    histogram structure. Unrelated roads must not produce a Repair Verified result.
    """
    hist_corr = _histogram_correlation(before_bgr, after_bgr)
    reasons: list[str] = []

    if alignment.method == "resize_fallback" and alignment.match_count < MIN_SAME_SCENE_MATCHES:
        reasons.append(
            f"feature matches too low ({alignment.match_count} < {MIN_SAME_SCENE_MATCHES})"
        )
    if not alignment.success and alignment.inlier_ratio < MIN_SAME_SCENE_INLIER_RATIO:
        reasons.append(
            f"alignment inliers too low ({alignment.inlier_ratio:.2f})"
        )
    if hist_corr < MIN_HISTOGRAM_CORR and not alignment.success:
        reasons.append(f"image appearance mismatch (hist corr {hist_corr:.2f})")

    # Strong positive gate: successful alignment with enough matches → allow
    if (
        alignment.success
        and alignment.match_count >= MIN_SAME_SCENE_MATCHES
        and alignment.inlier_ratio >= MIN_SAME_SCENE_INLIER_RATIO
    ):
        return

    # Near-duplicate frames (same photo / tiny change) can still pass with
    # excellent histogram agreement AND enough raw matches, even if RANSAC is soft.
    if hist_corr >= 0.97 and alignment.match_count >= MIN_SAME_SCENE_MATCHES:
        return

    if reasons or not alignment.success:
        detail = "; ".join(reasons) if reasons else "scenes could not be aligned"
        raise VerificationError(
            "Before and after images do not appear to be the same road location. "
            "Use the after photo for this inspection’s road, not a different site. "
            f"({detail})"
        )


def _roi_and_context(
    roi: tuple[int, int, int, int], shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """Boolean masks for the damage box and a surrounding road ring of similar area."""
    h, w = shape
    x1, y1, x2, y2 = roi
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    roi_mask = np.zeros((h, w), bool)
    roi_mask[y1:y2, x1:x2] = True
    bw, bh = x2 - x1, y2 - y1
    pad_x, pad_y = int(bw * 0.6), int(bh * 0.6)
    ring = np.zeros((h, w), bool)
    ring[max(0, y1 - pad_y):min(h, y2 + pad_y), max(0, x1 - pad_x):min(w, x2 + pad_x)] = True
    ring &= ~roi_mask
    return roi_mask, ring


def _refine_local(
    before_n: np.ndarray,
    after_n: np.ndarray,
    valid: np.ndarray,
    window: tuple[int, int, int, int],
    align_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """
    ECC affine refinement of the after photo inside ``window`` (x1, y1, x2, y2).

    The global homography aligns the dominant road plane; near the damage a
    small residual shift (parallax, lens distortion) is enough to look like
    "change". Alignment uses only ``align_mask`` pixels (the surrounding road,
    which should be unchanged), never the repaired spot itself.
    Returns (after_window, valid_window, converged).
    """
    x1, y1, x2, y2 = window
    b = before_n[y1:y2, x1:x2].astype(np.float32)
    a = after_n[y1:y2, x1:x2].astype(np.float32)
    v = valid[y1:y2, x1:x2].astype(np.uint8) * 255
    m = v if align_mask is None else (v & (align_mask[y1:y2, x1:x2].astype(np.uint8) * 255))
    if m.sum() < 255 * 500:
        return after_n[y1:y2, x1:x2], valid[y1:y2, x1:x2], False
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    try:
        _, warp = cv2.findTransformECC(b, a, warp, cv2.MOTION_AFFINE, criteria, m, 5)
    except cv2.error:
        return after_n[y1:y2, x1:x2], valid[y1:y2, x1:x2], False
    size = (x2 - x1, y2 - y1)
    flags = cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
    refined = cv2.warpAffine(after_n[y1:y2, x1:x2], warp, size, flags=flags)
    refined_valid = cv2.warpAffine(v, warp, size, flags=cv2.INTER_NEAREST + cv2.WARP_INVERSE_MAP)
    refined_valid = cv2.erode(refined_valid, np.ones((11, 11), np.uint8)) > 0
    return refined, refined_valid & valid[y1:y2, x1:x2], True


def compare_before_after(
    before_path: str | Path,
    after_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    verified_max: float = SSIM_VERIFIED_MAX,
    partial_max: float = SSIM_PARTIAL_MAX,
    roi: tuple[int, int, int, int] | None = None,
    require_same_scene: bool = True,
    max_side: int = MAX_SIDE_PX,
    damage_check=None,
) -> dict[str, Any]:
    """
    Full verification pipeline: downscale -> align -> same-scene gate ->
    masked SSIM (damage box vs surrounding road) -> classify.

    ``roi`` is the damage box (x1, y1, x2, y2) in the *original* before-image
    pixels (the detection's bbox). Without it only a whole-frame comparison is
    possible and the result always goes to manual review.

    ``damage_check(aligned_after_path, roi_scaled) -> bool`` may report that
    the detector still sees damage in the repaired area (forces review).

    Never downloads models. Uses OpenCV ORB only.
    Raises VerificationError when photos are clearly different roads.
    """
    before, scale = _read_bgr(before_path, max_side)
    after, _ = _read_bgr(after_path, max_side)
    alignment = align_images(before, after)
    aligned = alignment.aligned_after
    h, w = before.shape[:2]
    review_reasons: list[str] = []

    if require_same_scene:
        assert_same_road_scene(before, after, alignment)

    valid = alignment.valid_mask
    if valid is None:  # resize fallback: frames are not registered
        valid = np.ones((h, w), bool)
        review_reasons.append("photos could not be aligned")
    overlap = float(valid.mean())
    if overlap < MIN_OVERLAP:
        review_reasons.append(f"after photo covers only {overlap:.0%} of the before view")

    before_n = _normalize_lighting(_to_gray(before))
    after_n = _normalize_lighting(_to_gray(aligned))
    whole_ssim = compute_ssim(before_n, after_n, valid)
    if np.isnan(whole_ssim):
        whole_ssim = compute_ssim(before_n, after_n)

    roi_scaled = None
    roi_ssim = context_ssim = context_drop = None
    if roi is not None:
        roi_scaled = tuple(int(round(v * scale)) for v in roi)
        masks = _roi_and_context(roi_scaled, (h, w))
        if masks is None:
            roi_scaled = None
        else:
            roi_mask, ring = masks
            if (roi_mask & valid).sum() < 0.5 * roi_mask.sum():
                review_reasons.append("damage area is outside the after photo")
            ys, xs = np.nonzero(roi_mask | ring)
            window = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            wx1, wy1, wx2, wy2 = window
            after_w, valid_w, converged = _refine_local(
                before_n, after_n, valid, window, align_mask=ring
            )
            if not converged:
                review_reasons.append("could not fine-align the damage area")
            before_w = before_n[wy1:wy2, wx1:wx2]
            roi_w, ring_w = roi_mask[wy1:wy2, wx1:wx2], ring[wy1:wy2, wx1:wx2]
            roi_ssim = compute_ssim(before_w, after_w, roi_w & valid_w)
            context_ssim = compute_ssim(before_w, after_w, ring_w & valid_w)
            if np.isnan(roi_ssim):
                roi_ssim = None
            if context_ssim is not None and np.isnan(context_ssim):
                context_ssim = None
            if roi_ssim is not None and context_ssim is not None:
                context_drop = context_ssim - roi_ssim

    if roi_ssim is not None:
        ssim = roi_ssim
        decision = classify_ssim(
            ssim,
            verified_max=verified_max,
            partial_max=partial_max,
            alignment_success=alignment.success,
        )
        # The repaired spot must change clearly more than the road around it.
        if context_drop is not None and decision["status"] == STATUS_VERIFIED:
            if context_drop < PARTIAL_ROI_CONTEXT_DROP:
                decision["status"] = STATUS_NO_CHANGE
                decision["manual_review_required"] = True
                review_reasons.append(
                    "damage area changed no more than its surroundings (lighting/camera change?)"
                )
            elif context_drop < MIN_ROI_CONTEXT_DROP:
                decision["status"] = STATUS_PARTIAL
                decision["manual_review_required"] = True
                review_reasons.append("damage area changed only slightly more than its surroundings")
    else:
        ssim = whole_ssim
        decision = classify_ssim(
            ssim,
            verified_max=verified_max,
            partial_max=partial_max,
            alignment_success=alignment.success,
        )
        decision["manual_review_required"] = True
        review_reasons.append("no damage box available; whole-photo comparison only")

    aligned_path = None
    diff_path = None
    out = Path(output_dir) if output_dir is not None else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        aligned_path = str(out / "after_aligned.jpg")
        diff_path = str(out / "absdiff.jpg")
        cv2.imwrite(aligned_path, aligned)
        diff = cv2.absdiff(before_n, after_n)
        diff[~valid] = 0
        if roi_scaled is not None:
            diff = cv2.cvtColor(diff, cv2.COLOR_GRAY2BGR)
            cv2.rectangle(diff, roi_scaled[:2], roi_scaled[2:], (0, 0, 255), 2)
        cv2.imwrite(diff_path, diff)

    if damage_check is not None and roi_scaled is not None and aligned_path:
        try:
            if damage_check(aligned_path, roi_scaled):
                review_reasons.append("AI still detects damage in the repaired area")
                if decision["status"] == STATUS_VERIFIED:
                    decision["status"] = STATUS_PARTIAL
        except Exception as exc:  # detector problems must not block verification
            logger.warning("Post-repair damage check failed: %s", exc)

    if decision["status"] == STATUS_VERIFIED and not alignment.success:
        decision["status"] = STATUS_ALIGNMENT_FAILED
    manual_review = bool(decision["manual_review_required"] or review_reasons)

    return {
        "ssim": round(float(ssim), 6),
        "whole_frame_ssim": round(float(whole_ssim), 6),
        "roi_ssim": round(float(roi_ssim), 6) if roi_ssim is not None else None,
        "context_ssim": round(float(context_ssim), 6) if context_ssim is not None else None,
        "roi_change_vs_context": round(float(context_drop), 6) if context_drop is not None else None,
        "overlap_ratio": round(overlap, 4),
        "mean_absolute_diff": round(
            float(mean_absolute_diff(before_n * valid, after_n * valid)), 6
        ),
        "status": decision["status"],
        "completion_percentage": decision["completion_percentage"],
        "manual_review_required": manual_review,
        "review_reasons": review_reasons,
        "alignment": {
            "method": alignment.method,
            "success": alignment.success,
            "match_count": alignment.match_count,
            "inlier_ratio": alignment.inlier_ratio,
            "homography_available": alignment.homography is not None,
        },
        "thresholds": {
            **decision["thresholds"],
            "min_roi_context_drop": MIN_ROI_CONTEXT_DROP,
            "min_overlap": MIN_OVERLAP,
        },
        "roi": list(roi) if roi else None,
        "working_scale": round(scale, 4),
        "aligned_after_path": aligned_path,
        "diff_path": diff_path,
        "before_path": str(before_path),
        "after_path": str(after_path),
        "rule_version": RULE_VERSION,
        "metric_notes": (
            "SSIM is computed only where both photos overlap, after lighting "
            "normalisation. With a damage box, the repaired spot must change "
            "clearly more than the surrounding road to count as repaired."
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
