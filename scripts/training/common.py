"""Shared constants for the India road-damage training recipe."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # scripts/training/ -> project root
# Official RDD2022 India labels converted by name (scripts/training/convert_rdd_xml.py).
DEFAULT_ROAD = REPO_ROOT / "data" / "road_v2"
DEFAULT_EXTRA = REPO_ROOT / "data" / "india_extra"
DEFAULT_BUILD = REPO_ROOT / "data" / "build"
APP_MODELS = REPO_ROOT / "artifacts" / "ai_models"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Flask-compatible class ids — never reorder.
CLASS_NAMES = {
    0: "D00_longitudinal_crack",
    1: "D10_transverse_crack",
    2: "D20_alligator_crack",
    3: "D40_pothole",
}
SHORT = {0: "D00", 1: "D10", 2: "D20", 3: "D40"}

# Map arbitrary dataset class names (Roboflow / RDD variants) onto our ids.
_NAME_PATTERNS = [
    (3, re.compile(r"pot[\s_-]*hole|\bd40\b", re.I)),
    (2, re.compile(r"alligator|crocodile|fatigue|\bd20\b", re.I)),
    (1, re.compile(r"transverse|lateral|\bd10\b", re.I)),
    (0, re.compile(r"longitudinal|linear|\bd00\b", re.I)),
]


def map_class_name(name: str) -> int | None:
    """Canonical id for a dataset class name, or None to drop it."""
    for cid, pattern in _NAME_PATTERNS:
        if pattern.search(str(name)):
            return cid
    return None
