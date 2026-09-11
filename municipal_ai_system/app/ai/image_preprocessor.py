"""Optional image preprocessing helpers."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def ensure_rgb_jpeg(source: str | Path, destination: str | Path) -> str:
    """Convert source image to RGB JPEG without touching the original path object wrongly."""
    src = Path(source)
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as image:
        image.convert("RGB").save(dest, format="JPEG", quality=92)
    return str(dest)
