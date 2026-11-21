"""Mask geometry helpers for Phase 8 segmentation (pixel-space only)."""

from __future__ import annotations

from typing import Any

import numpy as np


def bbox_iou_xyxy(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def mask_to_bbox(mask: np.ndarray) -> list[float] | None:
    ys, xs = np.where(mask.astype(bool))
    if len(xs) == 0:
        return None
    return [float(xs.min()), float(ys.min()), float(xs.max()) + 1, float(ys.max()) + 1]


def mask_pixel_count(mask: np.ndarray) -> int:
    return int(np.count_nonzero(mask))


def damaged_area_percentage(pixel_count: int, width: int, height: int) -> float:
    total = max(1, int(width) * int(height))
    return round(100.0 * float(pixel_count) / float(total), 6)


def union_mask_pixel_count(masks: list[np.ndarray], height: int, width: int) -> int:
    if not masks:
        return 0
    union = np.zeros((height, width), dtype=bool)
    for mask in masks:
        if mask is None:
            continue
        arr = np.asarray(mask).astype(bool)
        if arr.shape != (height, width):
            # Skip mismatched shapes rather than failing the batch.
            continue
        union |= arr
    return int(np.count_nonzero(union))


def resize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    import cv2

    if mask.shape[0] == height and mask.shape[1] == width:
        return (mask > 0).astype(np.uint8)
    resized = cv2.resize(
        mask.astype(np.uint8),
        (int(width), int(height)),
        interpolation=cv2.INTER_NEAREST,
    )
    return (resized > 0).astype(np.uint8)


def mask_to_polygon(mask: np.ndarray, simplify_eps: float = 2.0) -> list[list[float]]:
    """Return outer contour polygon [[x,y], ...] or [] if empty/malformed."""
    import cv2

    binary = (np.asarray(mask) > 0).astype(np.uint8) * 255
    if binary.ndim != 2 or not np.any(binary):
        return []
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    if simplify_eps > 0:
        contour = cv2.approxPolyDP(contour, simplify_eps, True)
    pts = contour.reshape(-1, 2)
    if len(pts) < 3:
        return []
    return [[float(x), float(y)] for x, y in pts]


def polygon_normalized(polygon: list[list[float]], width: int, height: int) -> list[list[float]]:
    w = max(1, int(width))
    h = max(1, int(height))
    return [[round(x / w, 6), round(y / h, 6)] for x, y in polygon]


def classes_compatible(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    aa = a.lower().replace(" ", "_")
    bb = b.lower().replace(" ", "_")
    if aa == bb:
        return True
    # Friendly vs RDD names
    tokens = ("longitudinal", "transverse", "alligator", "pothole", "crack")
    return any(t in aa and t in bb for t in tokens)


def match_mask_to_detections(
    mask_result: dict[str, Any],
    detections: list[dict[str, Any]],
    *,
    min_iou: float = 0.1,
) -> int | None:
    """Return matched detection index or None. Never raises."""
    if not detections:
        return None
    bbox = mask_result.get("bbox")
    if not bbox or len(bbox) != 4:
        return None
    class_name = mask_result.get("class_name") or mask_result.get("damage_type")
    best_idx = None
    best_score = -1.0
    for idx, det in enumerate(detections):
        det_bbox = det.get("bbox_xyxy")
        if not det_bbox or len(det_bbox) != 4:
            continue
        if not classes_compatible(class_name, det.get("class_name") or det.get("damage_type")):
            continue
        iou = bbox_iou_xyxy([float(v) for v in bbox], [float(v) for v in det_bbox])
        conf = float(det.get("confidence") or 0.0)
        score = iou + 0.01 * conf
        if iou >= min_iou and score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


def associate_masks_with_detections(
    masks: list[dict[str, Any]],
    detections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach matched_detection_id when possible; leave null on failure."""
    associated: list[dict[str, Any]] = []
    for mask in masks:
        item = dict(mask)
        idx = match_mask_to_detections(item, detections)
        if idx is None:
            item["matched_detection_id"] = None
            item["matched_detection_index"] = None
        else:
            det = detections[idx]
            item["matched_detection_index"] = idx
            item["matched_detection_id"] = det.get("detection_id") or det.get("id") or idx
            # Copy class from detection when mask class unknown
            if not item.get("class_name") and det.get("class_name"):
                item["class_name"] = det["class_name"]
            if not item.get("damage_type") and det.get("damage_type"):
                item["damage_type"] = det["damage_type"]
        associated.append(item)
    return associated
