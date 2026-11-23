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
# Pixels of the before frame that the warped after photo must cover for a
# whole-photo comparison. With a damage box, what matters is the box itself:
# stepping closer legitimately covers less of the before view.
MIN_OVERLAP = 0.6
MIN_ALIGN_OVERLAP = 0.35   # below this the fit is not trusted at all
MIN_ROI_COVERAGE = 0.8     # share of the damage box the after photo must show
# Damage region must change this much more than its surroundings (SSIM points)
# before a repair can be auto-verified; lighting/time-of-day changes hit both.
MIN_ROI_CONTEXT_DROP = 0.15
PARTIAL_ROI_CONTEXT_DROP = 0.08
# Lighting / exposure changes survive lighting normalisation with moderate SSIM;
# near-zero SSIM means the surface itself was replaced. When the damage area AND
# its surrounding road are both replaced, the road was resurfaced: the ring is no
# longer an unchanged reference, so the relative-drop guard does not apply.
STRONG_CHANGE_SSIM = 0.25
RESURFACED_CONTEXT_SSIM = 0.35

# Same-scene gate: different roads usually fail ORB matching hard.
MIN_SAME_SCENE_MATCHES = 12
MIN_SAME_SCENE_INLIER_RATIO = 0.12
MIN_HISTOGRAM_CORR = 0.35

# Same-spot fallback: repeat photos from (almost) the same position, where ORB fails
# because the repair removed the road texture (e.g. full resurfacing) and the few
# matches sit on trees/sky. Accept a direct ECC alignment only if it correlates
# well and stays close to identity; photos of different roads do not converge.
ECC_MIN_CORRELATION = 0.30
ECC_MAX_SCALE_DELTA = 0.25   # |scale - 1|
ECC_MAX_SHEAR = 0.15         # off-diagonal terms of the affine matrix
ECC_MAX_SHIFT_FRAC = 0.20    # translation as a fraction of the frame size

# A homography that folds, flips or wildly rescales the frame is a bad fit (often a
# few matches on distant trees), not a different camera angle.
MAX_WARP_AREA_RATIO = 4.0
# Model selection: keep the 8-dof homography only when it explains clearly more
# matches than a 6-dof affine fit. Matches often cluster in one part of the frame
# (trees, buildings); perspective terms fitted there extrapolate badly to the road
# near the camera, while an affine fit stays well-behaved.
HOMOGRAPHY_MIN_GAIN = 1.10


class VerificationError(Exception):
    """Controlled verification failure."""


@dataclass
class AlignmentResult:
    aligned_after: np.ndarray
    homography: np.ndarray | None
    method: str  # "sift_homography[+ecc]" | "orb_homography[+ecc]" | "ecc_same_viewpoint" | "resize_fallback"
    inlier_ratio: float
    match_count: int
    success: bool
    valid_mask: np.ndarray | None = None  # pixels the after photo actually covers
    ecc_correlation: float | None = None  # set by the same-viewpoint fallback


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


def _ecc_same_viewpoint(
    before_bgr: np.ndarray, after_bgr: np.ndarray, match_count: int
) -> AlignmentResult | None:
    """Direct (ECC affine) alignment from identity for photos taken from the same spot."""
    h, w = before_bgr.shape[:2]
    resized = cv2.resize(after_bgr, (w, h), interpolation=cv2.INTER_AREA)
    gb = cv2.GaussianBlur(_normalize_lighting(_to_gray(before_bgr)), (5, 5), 0).astype(np.float32)
    ga = cv2.GaussianBlur(_normalize_lighting(_to_gray(resized)), (5, 5), 0).astype(np.float32)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    try:
        cc, warp = cv2.findTransformECC(gb, ga, warp, cv2.MOTION_AFFINE, criteria, None, 5)
    except cv2.error:
        return None
    a, b, tx = float(warp[0, 0]), float(warp[0, 1]), float(warp[0, 2])
    c, d, ty = float(warp[1, 0]), float(warp[1, 1]), float(warp[1, 2])
    near_identity = (
        abs(a - 1) <= ECC_MAX_SCALE_DELTA
        and abs(d - 1) <= ECC_MAX_SCALE_DELTA
        and abs(b) <= ECC_MAX_SHEAR
        and abs(c) <= ECC_MAX_SHEAR
        and abs(tx) <= ECC_MAX_SHIFT_FRAC * w
        and abs(ty) <= ECC_MAX_SHIFT_FRAC * h
    )
    if cc < ECC_MIN_CORRELATION or not near_identity:
        return None
    flags = cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
    aligned = cv2.warpAffine(resized, warp, (w, h), flags=flags)
    coverage = cv2.warpAffine(
        np.full((h, w), 255, np.uint8), warp, (w, h), flags=cv2.INTER_NEAREST + cv2.WARP_INVERSE_MAP
    )
    coverage = cv2.erode(coverage, np.ones((11, 11), np.uint8))
    homography = np.vstack([cv2.invertAffineTransform(warp), [0.0, 0.0, 1.0]])
    return AlignmentResult(
        aligned_after=aligned,
        homography=homography,
        method="ecc_same_viewpoint",
        inlier_ratio=0.0,
        match_count=match_count,
        success=True,
        valid_mask=coverage > 0,
        ecc_correlation=round(float(cc), 4),
    )


def _homography_is_sane(homography: np.ndarray, after_shape: tuple[int, ...], before_shape: tuple[int, ...]) -> bool:
    """Reject folded / mirrored / degenerate fits; real angle changes keep a convex, same-orientation quad."""
    ah, aw = after_shape[:2]
    bh, bw = before_shape[:2]
    corners = np.float32([[0, 0], [aw, 0], [aw, ah], [0, ah]]).reshape(-1, 1, 2)
    try:
        quad = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    except cv2.error:
        return False
    if not np.all(np.isfinite(quad)) or not cv2.isContourConvex(quad.astype(np.float32)):
        return False
    signed = 0.5 * float(np.sum(quad[:, 0] * np.roll(quad[:, 1], -1) - np.roll(quad[:, 0], -1) * quad[:, 1]))
    if signed <= 0:  # mirrored
        return False
    ratio = signed / float(bw * bh)
    return 1.0 / MAX_WARP_AREA_RATIO <= ratio <= MAX_WARP_AREA_RATIO


ECC_REFINE_MAX_SIDE = 640


def _scale_matrix(factor: float) -> np.ndarray:
    return np.diag([factor, factor, 1.0])


def _refine_homography_ecc(
    before_bgr: np.ndarray, after_bgr: np.ndarray, result: AlignmentResult
) -> AlignmentResult:
    """
    Polish a feature homography with dense ECC over the whole frame.

    Feature matches cluster where texture survives (often trees or buildings far
    away); a homography fitted there extrapolates poorly to the road near the
    camera. ECC uses every pixel (kerbs, markings, verge, scenery) and starts from
    the feature fit, so it converges to the same scene - never to a different one.
    Kept only if it correlates better and stays sane.
    """
    if result.homography is None:
        return result
    bh, bw = before_bgr.shape[:2]
    ah, aw = after_bgr.shape[:2]
    sb = min(1.0, ECC_REFINE_MAX_SIDE / max(bh, bw))
    sa = min(1.0, ECC_REFINE_MAX_SIDE / max(ah, aw))

    def prep(img, f):
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
        return cv2.GaussianBlur(_normalize_lighting(_to_gray(small)), (5, 5), 0).astype(np.float32)

    tb, ta = prep(before_bgr, sb), prep(after_bgr, sa)
    # ECC warp maps before (template) pixels -> after (input) pixels.
    init = (_scale_matrix(sa) @ np.linalg.inv(result.homography) @ np.linalg.inv(_scale_matrix(sb)))
    init = (init / init[2, 2]).astype(np.float32)
    size = (tb.shape[1], tb.shape[0])
    flags = cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
    try:
        start_cc = cv2.computeECC(tb, cv2.warpPerspective(ta, init, size, flags=flags))
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 150, 1e-6)
        cc, warp = cv2.findTransformECC(tb, ta, init.copy(), cv2.MOTION_HOMOGRAPHY, criteria, None, 5)
    except cv2.error:
        return result
    if cc <= start_cc:
        return result
    refined_inv = np.linalg.inv(_scale_matrix(sa)) @ warp.astype(np.float64) @ _scale_matrix(sb)
    homography = np.linalg.inv(refined_inv)
    homography /= homography[2, 2]
    if not _homography_is_sane(homography, after_bgr.shape, before_bgr.shape):
        return result
    warped = cv2.warpPerspective(after_bgr, homography, (bw, bh))
    coverage = cv2.warpPerspective(
        np.full((ah, aw), 255, np.uint8), homography, (bw, bh), flags=cv2.INTER_NEAREST
    )
    coverage = cv2.erode(coverage, np.ones((11, 11), np.uint8))
    return AlignmentResult(
        aligned_after=warped,
        homography=homography,
        method=result.method + "+ecc",
        inlier_ratio=result.inlier_ratio,
        match_count=result.match_count,
        success=result.success,
        valid_mask=coverage > 0,
        ecc_correlation=round(float(cc), 4),
    )


def _is_good(result: AlignmentResult) -> bool:
    return (
        result.success
        and result.valid_mask is not None
        and float(result.valid_mask.mean()) >= MIN_ALIGN_OVERLAP
    )


def align_images(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    *,
    max_features: int = 2000,
    min_matches: int = 12,
    ransac_reproj: float = 5.0,
) -> AlignmentResult:
    """
    Register the after photo onto the before photo, tolerating a different angle.

    1. SIFT + MAGSAC++ homography (scale/rotation invariant: phone held higher,
       closer, rotated or tilted; works off kerbs, markings, trees, buildings)
    2. ORB + RANSAC homography (previous method)
    3. Same-viewpoint ECC fit (repair removed the road texture both rely on)
    The first good fit wins; otherwise the best partial result is returned so the
    same-scene gate can reject it.
    """
    candidates: list[AlignmentResult] = []
    for method in ("sift", "orb"):
        result = _align_features(
            before_bgr, after_bgr, method=method,
            max_features=max_features, min_matches=min_matches, ransac_reproj=ransac_reproj,
        )
        if _is_good(result):
            return _refine_homography_ecc(before_bgr, after_bgr, result)
        candidates.append(result)
    best_matches = max(c.match_count for c in candidates)
    fallback = _ecc_same_viewpoint(before_bgr, after_bgr, best_matches)
    if fallback is not None:
        return fallback
    # Prefer a sane homography over a resize fallback for the gate / review reasons.
    candidates.sort(key=lambda c: (c.success, c.homography is not None, c.match_count), reverse=True)
    return candidates[0]


def _align_features(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    *,
    method: str = "orb",
    max_features: int = 2000,
    min_matches: int = 12,
    ransac_reproj: float = 5.0,
) -> AlignmentResult:
    """
    Align `after` into `before` coordinate space via SIFT or ORB + homography.

    Falls back to resize if matching fails.
    """
    h, w = before_bgr.shape[:2]
    before_gray = _normalize_lighting(_to_gray(before_bgr))
    after_gray = _normalize_lighting(_to_gray(after_bgr))

    if method == "sift":
        detector = cv2.SIFT_create(nfeatures=max_features * 2)
        norm, label, robust = cv2.NORM_L2, "sift_homography", getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    else:
        detector = cv2.ORB_create(nfeatures=max_features)
        norm, label, robust = cv2.NORM_HAMMING, "orb_homography", cv2.RANSAC
    kp1, des1 = detector.detectAndCompute(before_gray, None)
    kp2, des2 = detector.detectAndCompute(after_gray, None)

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

    matcher = cv2.BFMatcher(norm, crossCheck=False)
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
    homography, mask = cv2.findHomography(dst_pts, src_pts, robust, ransac_reproj)
    if homography is not None:
        affine, affine_mask = cv2.estimateAffine2D(
            dst_pts, src_pts, method=cv2.RANSAC, ransacReprojThreshold=ransac_reproj
        )
        h_inliers = int(mask.sum()) if mask is not None else 0
        a_inliers = int(affine_mask.sum()) if affine_mask is not None else 0
        if affine is not None and h_inliers < HOMOGRAPHY_MIN_GAIN * a_inliers:
            homography, mask = np.vstack([affine, [0.0, 0.0, 1.0]]), affine_mask
            label = label.replace("homography", "affine")
    if homography is not None and not _homography_is_sane(homography, after_bgr.shape, before_bgr.shape):
        homography = None
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
        method=label,
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

    # Same-viewpoint ECC fit (repair removed the road texture ORB relies on): the
    # direct fit itself is the strong evidence, backed by a few ORB matches and a
    # similar overall appearance.
    if (
        alignment.method == "ecc_same_viewpoint"
        and alignment.success
        and (alignment.ecc_correlation or 0.0) >= ECC_MIN_CORRELATION
        and alignment.match_count >= MIN_SAME_SCENE_MATCHES // 2
        and hist_corr >= MIN_HISTOGRAM_CORR
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
    if overlap < (MIN_OVERLAP if roi is None else MIN_ALIGN_OVERLAP):
        review_reasons.append(f"after photo covers only {overlap:.0%} of the before view")

    before_n = _normalize_lighting(_to_gray(before))
    after_n = _normalize_lighting(_to_gray(aligned))
    whole_ssim = compute_ssim(before_n, after_n, valid)
    if np.isnan(whole_ssim):
        whole_ssim = compute_ssim(before_n, after_n)

    roi_scaled = None
    roi_ssim = context_ssim = context_drop = None
    fine_align_failed = False
    roi_coverage = 0.0
    notes: list[str] = []
    if roi is not None:
        roi_scaled = tuple(int(round(v * scale)) for v in roi)
        masks = _roi_and_context(roi_scaled, (h, w))
        if masks is None:
            roi_scaled = None
        else:
            roi_mask, ring = masks
            roi_coverage = float((roi_mask & valid).sum()) / float(roi_mask.sum())
            if roi_coverage < MIN_ROI_COVERAGE:
                review_reasons.append(
                    f"after photo shows only {roi_coverage:.0%} of the damage area"
                )
            ys, xs = np.nonzero(roi_mask | ring)
            window = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            wx1, wy1, wx2, wy2 = window
            after_w, valid_w, converged = _refine_local(
                before_n, after_n, valid, window, align_mask=ring
            )
            fine_align_failed = not converged
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
        resurfaced = (
            alignment.success
            and roi_coverage >= MIN_ROI_COVERAGE
            and roi_ssim < STRONG_CHANGE_SSIM
            and context_ssim is not None
            and context_ssim < RESURFACED_CONTEXT_SSIM
        )
        if resurfaced:
            notes.append("surrounding road was resurfaced too; compared against the original surface")
        elif fine_align_failed:
            review_reasons.append("could not fine-align the damage area")
        # The repaired spot must change clearly more than the road around it.
        if context_drop is not None and decision["status"] == STATUS_VERIFIED and not resurfaced:
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
        "roi_coverage": round(roi_coverage, 4) if roi_scaled is not None else None,
        "mean_absolute_diff": round(
            float(mean_absolute_diff(before_n * valid, after_n * valid)), 6
        ),
        "status": decision["status"],
        "completion_percentage": decision["completion_percentage"],
        "manual_review_required": manual_review,
        "review_reasons": review_reasons,
        "notes": notes,
        "alignment": {
            "method": alignment.method,
            "success": alignment.success,
            "match_count": alignment.match_count,
            "inlier_ratio": alignment.inlier_ratio,
            "homography_available": alignment.homography is not None,
            "ecc_correlation": alignment.ecc_correlation,
        },
        "thresholds": {
            **decision["thresholds"],
            "min_roi_context_drop": MIN_ROI_CONTEXT_DROP,
            "min_overlap": MIN_OVERLAP,
            "min_roi_coverage": MIN_ROI_COVERAGE,
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
