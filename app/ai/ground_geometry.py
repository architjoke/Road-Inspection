"""Estimate real-world damage size from a bounding box (flat-road pinhole model).

A single photo has no absolute scale, so we assume a camera pose:

* camera height above the road (``CAMERA_HEIGHT_M``, handheld default 1.4 m)
* downward tilt of the optical axis (``CAMERA_PITCH_DEG``, default 30 deg)
* focal length from EXIF ``FocalLengthIn35mmFilm`` when the phone records it,
  else a horizontal field of view (``CAMERA_HFOV_DEG``, default 66 deg)

Each box corner is projected onto the road plane. Results are *estimates*
(typically +/-30-40% when the pose assumption is right) and are always labelled
as such. Damage near or above the horizon, or very far away, is flagged
unreliable so severity falls back and asks for manual review.
"""

from __future__ import annotations

import math
import os
from typing import Any

# Full-frame 35 mm diagonal in mm (used to convert 35 mm-equivalent focal length).
FULL_FRAME_DIAGONAL_MM = 43.2666

DEFAULT_CAMERA = {
    "height_m": 1.4,
    "pitch_deg": 30.0,
    "hfov_deg": 66.0,
    "max_range_m": 20.0,
    # Video is usually dashcam / vehicle-mounted: lower tilt.
    "video_height_m": 1.3,
    "video_pitch_deg": 12.0,
}

# Rays this close to horizontal (sin of angle below horizon) are not trusted.
_MIN_DOWN = 0.03


def _cfg_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get(name) is not None:
            return float(current_app.config[name])
    except Exception:
        pass
    return default


def camera_defaults() -> dict[str, float]:
    return {
        "height_m": _cfg_float("CAMERA_HEIGHT_M", DEFAULT_CAMERA["height_m"]),
        "pitch_deg": _cfg_float("CAMERA_PITCH_DEG", DEFAULT_CAMERA["pitch_deg"]),
        "hfov_deg": _cfg_float("CAMERA_HFOV_DEG", DEFAULT_CAMERA["hfov_deg"]),
        "max_range_m": _cfg_float("CAMERA_MAX_RANGE_M", DEFAULT_CAMERA["max_range_m"]),
        "video_height_m": _cfg_float("CAMERA_VIDEO_HEIGHT_M", DEFAULT_CAMERA["video_height_m"]),
        "video_pitch_deg": _cfg_float(
            "CAMERA_VIDEO_PITCH_DEG", DEFAULT_CAMERA["video_pitch_deg"]
        ),
    }


def build_camera(
    width: float,
    height: float,
    *,
    focal_length_35mm: float | None = None,
    height_m: float | None = None,
    pitch_deg: float | None = None,
    video: bool = False,
) -> dict[str, Any]:
    """Camera intrinsics + assumed pose for an upright image of width x height."""
    d = camera_defaults()
    if video:
        height_m = d["video_height_m"] if height_m is None else height_m
        pitch_deg = d["video_pitch_deg"] if pitch_deg is None else pitch_deg
    w, h = float(width), float(height)
    if focal_length_35mm and focal_length_35mm > 5:
        focal_px = float(focal_length_35mm) * math.hypot(w, h) / FULL_FRAME_DIAGONAL_MM
        focal_source = "exif_35mm"
    else:
        half_fov = math.radians(d["hfov_deg"]) / 2.0
        focal_px = (max(w, h) / 2.0) / math.tan(half_fov)
        focal_source = "assumed_fov"
    return {
        "width": w,
        "height": h,
        "cx": w / 2.0,
        "cy": h / 2.0,
        "focal_px": focal_px,
        "focal_source": focal_source,
        "height_m": float(height_m if height_m is not None else d["height_m"]),
        "pitch_deg": float(pitch_deg if pitch_deg is not None else d["pitch_deg"]),
        "pose_source": (
            "assumed_video"
            if video
            else ("assumed" if height_m is None and pitch_deg is None else "provided")
        ),
        "max_range_m": d["max_range_m"],
    }


def _project(u: float, v: float, cam: dict[str, Any]) -> tuple[float, float] | None:
    """Pixel -> (lateral_m, forward_m) on the road plane; None above horizon."""
    f = cam["focal_px"]
    x = (u - cam["cx"]) / f
    y = (v - cam["cy"]) / f
    th = math.radians(cam["pitch_deg"])
    down = math.sin(th) + y * math.cos(th)
    norm = math.sqrt(1.0 + x * x + y * y)
    if down / norm <= _MIN_DOWN:
        return None
    t = cam["height_m"] / down
    forward = (math.cos(th) - y * math.sin(th)) * t
    return x * t, forward


def horizon_row(cam: dict[str, Any]) -> float:
    """Image row (v) of the lowest still-trusted ray."""
    th = math.radians(cam["pitch_deg"])
    # down/|ray| == _MIN_DOWN, approximated at the image centre column
    angle = math.asin(_MIN_DOWN)
    return cam["cy"] - cam["focal_px"] * math.tan(th - angle)


def measure_bbox(bbox_xyxy: list[float], cam: dict[str, Any]) -> dict[str, Any]:
    """Estimated ground footprint of a box (metres)."""
    result: dict[str, Any] = {
        "reliable": False,
        "width_m": None,
        "length_m": None,
        "box_area_m2": None,
        "distance_m": None,
        "reasons": [],
        "camera": {
            k: (round(v, 3) if isinstance(v, float) else v)
            for k, v in cam.items()
            if k in {"focal_px", "focal_source", "height_m", "pitch_deg", "pose_source"}
        },
    }
    if not bbox_xyxy or len(bbox_xyxy) != 4:
        result["reasons"].append("no_bbox")
        return result

    x1, y1, x2, y2 = (float(v) for v in bbox_xyxy)
    if x2 <= x1 or y2 <= y1:
        result["reasons"].append("degenerate_bbox")
        return result

    v_min = horizon_row(cam) + 2.0
    if y2 <= v_min:
        result["reasons"].append("above_horizon")
        return result
    truncated = y1 < v_min
    if truncated:
        y1 = v_min
        result["reasons"].append("top_clipped_at_horizon")

    bl, br = _project(x1, y2, cam), _project(x2, y2, cam)
    tl, tr = _project(x1, y1, cam), _project(x2, y1, cam)
    bc, tc = _project((x1 + x2) / 2, y2, cam), _project((x1 + x2) / 2, y1, cam)
    mid = _project((x1 + x2) / 2, (y1 + y2) / 2, cam)
    if None in (bl, br, tl, tr, bc, tc, mid):
        result["reasons"].append("projection_failed")
        return result

    width_bottom = abs(br[0] - bl[0])
    width_top = abs(tr[0] - tl[0])
    width_m = (width_bottom + width_top) / 2.0
    length_m = max(0.0, tc[1] - bc[1])
    distance_m = math.hypot(mid[0], mid[1])

    result.update(
        {
            "width_m": round(width_m, 3),
            "length_m": round(length_m, 3),
            "box_area_m2": round(width_m * length_m, 4),
            "distance_m": round(distance_m, 2),
        }
    )

    reliable = True
    if distance_m > cam["max_range_m"]:
        reliable = False
        result["reasons"].append("too_far_from_camera")
    if max(width_m, length_m) > 25.0:
        reliable = False
        result["reasons"].append("implausible_size")
    if truncated and (y2 - y1) < 4:
        reliable = False
    result["reliable"] = reliable
    return result


__all__ = ["build_camera", "camera_defaults", "measure_bbox", "horizon_row"]
