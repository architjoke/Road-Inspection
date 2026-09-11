"""Draw detection annotations without overwriting originals."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "SEVERE": (220, 38, 38),
    "MODERATE": (234, 88, 12),
    "MINOR": (22, 163, 74),
}


def _color_for(det: dict[str, Any]) -> tuple[int, int, int]:
    severity = (det.get("severity") or "").upper()
    if severity in SEVERITY_COLORS:
        return SEVERITY_COLORS[severity]
    damage = (det.get("damage_type") or "").lower()
    if "pothole" in damage or "alligator" in damage:
        return SEVERITY_COLORS["SEVERE"]
    if "crack" in damage:
        return SEVERITY_COLORS["MODERATE"]
    return (37, 99, 235)


def _ensure_dest(source: Path, output_path: str | Path) -> Path:
    dest = Path(output_path)
    if dest.resolve() == source.resolve():
        raise ValueError("Annotated output must not overwrite the original media.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def annotate_detections(
    source_image: str | Path,
    detections: list[dict[str, Any]],
    output_path: str | Path,
) -> str:
    """
    Write an annotated copy to output_path.

    Never modifies the source file.
    """
    src = Path(source_image)
    dest = _ensure_dest(src, output_path)

    with Image.open(src) as image:
        canvas = image.convert("RGB").copy()
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        for det in detections:
            x1, y1, x2, y2 = det["bbox_xyxy"]
            color = _color_for(det)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            label = (
                f"{det.get('damage_type') or det.get('class_name')} "
                f"{float(det.get('confidence', 0)):.2f}"
            )
            text_y = max(0, y1 - 14)
            draw.text((x1 + 2, text_y), label, fill=color, font=font)

        canvas.save(dest, format="JPEG", quality=90)

    logger.info("Wrote annotated image to %s", dest)
    return str(dest)


def annotate_segmentation(
    source_image: str | Path,
    segmentation_results: list[dict[str, Any]],
    output_path: str | Path,
    *,
    detections: list[dict[str, Any]] | None = None,
    alpha: float = 0.45,
) -> str:
    """Transparent mask overlay + polygon + optional detection boxes."""
    src = Path(source_image)
    dest = _ensure_dest(src, output_path)

    with Image.open(src) as image:
        base = image.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        for item in segmentation_results:
            color = _color_for(item)
            mask = item.get("mask")
            if mask is not None:
                arr = np.asarray(mask).astype(bool)
                if arr.shape[0] == base.size[1] and arr.shape[1] == base.size[0]:
                    tint = Image.new("RGBA", base.size, (*color, int(255 * alpha)))
                    mask_img = Image.fromarray((arr.astype(np.uint8) * 255), mode="L")
                    overlay.paste(tint, (0, 0), mask_img)
            polygon = item.get("polygon") or []
            if len(polygon) >= 3:
                flat = [(float(p[0]), float(p[1])) for p in polygon]
                draw.line(flat + [flat[0]], fill=(*color, 255), width=2)
            bbox = item.get("bbox")
            if bbox and len(bbox) == 4:
                draw.rectangle(bbox, outline=(*color, 255), width=2)
            label = (
                f"{item.get('class_name')} {float(item.get('confidence', 0)):.2f} "
                f"{float(item.get('damaged_area_percentage', 0)):.2f}%px"
            )
            if bbox:
                draw.text(
                    (bbox[0] + 2, max(0, bbox[1] - 14)),
                    label,
                    fill=(*color, 255),
                    font=font,
                )

        if detections:
            box_draw = ImageDraw.Draw(overlay)
            for det in detections:
                bbox = det.get("bbox_xyxy")
                if not bbox:
                    continue
                box_draw.rectangle(bbox, outline=(255, 255, 255, 200), width=1)

        composed = Image.alpha_composite(base, overlay).convert("RGB")
        composed.save(dest, format="JPEG", quality=90)

    logger.info("Wrote segmentation overlay to %s", dest)
    return str(dest)


def annotate_depth_map(
    source_image: str | Path,
    depth_map: np.ndarray,
    output_path: str | Path,
    *,
    regions: list[dict[str, Any]] | None = None,
) -> str:
    """Write a relative-depth colormap visualization (not metric)."""
    src = Path(source_image)
    dest = _ensure_dest(src, output_path)

    depth = np.asarray(depth_map, dtype=np.float32)
    if depth.ndim != 2:
        raise ValueError("depth_map must be 2D")
    scaled = (np.clip(depth, 0, 1) * 255).astype(np.uint8)
    # Vectorized iron-like colormap (no matplotlib)
    r = np.zeros_like(scaled, dtype=np.uint8)
    g = np.zeros_like(scaled, dtype=np.uint8)
    b = np.zeros_like(scaled, dtype=np.uint8)
    m1 = scaled < 85
    m2 = (scaled >= 85) & (scaled < 170)
    m3 = scaled >= 170
    r[m1], g[m1], b[m1] = 0, np.minimum(255, scaled[m1] * 3), 255
    r[m2], g[m2], b[m2] = 0, 255, np.maximum(0, 255 - (scaled[m2] - 85) * 3)
    r[m3] = np.minimum(255, (scaled[m3] - 170) * 3)
    g[m3] = np.maximum(0, 255 - ((scaled[m3] - 170) * 3) // 2)
    b[m3] = 0
    heat = Image.fromarray(np.stack([r, g, b], axis=-1), mode="RGB")

    with Image.open(src) as image:
        base = image.convert("RGB").resize(heat.size)
        blended = Image.blend(base, heat, alpha=0.55)
        draw = ImageDraw.Draw(blended)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.text(
            (8, 8),
            "relative depth (not metric; calibration unavailable)",
            fill=(255, 255, 255),
            font=font,
        )
        _ = regions
        blended.save(dest, format="JPEG", quality=90)

    logger.info("Wrote depth visualization to %s", dest)
    return str(dest)
