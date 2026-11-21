"""EXIF orientation + camera metadata helpers.

Phones store portrait photos as landscape pixels plus an EXIF Orientation tag.
OpenCV (used by Ultralytics) applies that tag when reading; PIL does not. If the
two disagree, YOLO boxes are drawn on a rotated canvas. Everything in the
analysis pipeline therefore works on an *upright* copy with Orientation=1.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

EXIF_ORIENTATION = 0x0112
EXIF_IFD = 0x8769
EXIF_FOCAL_LENGTH = 0x920A
EXIF_FOCAL_LENGTH_35MM = 0xA405

# Orientations 5-8 swap width and height.
_TRANSPOSING = {5, 6, 7, 8}


def exif_orientation(image: Image.Image) -> int:
    try:
        return int(image.getexif().get(EXIF_ORIENTATION, 1) or 1)
    except Exception:
        return 1


def upright_size(path: str | Path) -> tuple[int, int]:
    """(width, height) as OpenCV/YOLO will see the image (EXIF applied)."""
    with Image.open(path) as image:
        width, height = image.size
        if exif_orientation(image) in _TRANSPOSING:
            return height, width
        return width, height


def ensure_upright(path: str | Path) -> Path:
    """
    Return a path whose pixels are already upright (Orientation=1).

    Writes ``<stem>_upright.jpg`` next to the original only when needed; the
    original upload is never modified. EXIF (GPS, focal length) is preserved
    with the orientation reset to 1.
    """
    src = Path(path)
    try:
        with Image.open(src) as image:
            orientation = exif_orientation(image)
            if orientation in (0, 1):
                return src
            exif = image.getexif()
            upright = ImageOps.exif_transpose(image)
            exif[EXIF_ORIENTATION] = 1
            dest = src.with_name(f"{src.stem}_upright.jpg")
            upright.convert("RGB").save(dest, format="JPEG", quality=95, exif=exif.tobytes())
            logger.info("Normalized EXIF orientation %s for %s", orientation, src.name)
            return dest
    except Exception:
        logger.exception("Could not normalize orientation for %s; using original", src)
        return src


def camera_metadata(path: str | Path) -> dict[str, Any]:
    """Focal-length info from EXIF (None values when absent)."""
    meta: dict[str, Any] = {"focal_length_35mm": None, "focal_length_mm": None}
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            sub = exif.get_ifd(EXIF_IFD) if hasattr(exif, "get_ifd") else {}
            f35 = sub.get(EXIF_FOCAL_LENGTH_35MM) or exif.get(EXIF_FOCAL_LENGTH_35MM)
            fmm = sub.get(EXIF_FOCAL_LENGTH) or exif.get(EXIF_FOCAL_LENGTH)
            if f35:
                meta["focal_length_35mm"] = float(f35)
            if fmm:
                meta["focal_length_mm"] = float(fmm)
    except Exception:
        pass
    return meta
