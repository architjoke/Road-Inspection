"""Phase 9 monocular depth estimation (optional; relative depth only)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_depth_model: Any = None
_depth_model_path: str | None = None
_depth_load_count = 0

# Representation note:
# Mock and optional local adapters produce *relative* depth maps normalized to [0, 1]
# where larger values mean relatively closer surfaces in the mock convention
# (inverse-depth-like). This is NOT metric depth. Physical mm/cm/m and volumes
# are unavailable without camera calibration.


class DepthError(Exception):
    pass


class DepthDisabled(DepthError):
    pass


class DepthModelMissing(DepthError):
    pass


class DepthInvalidImage(DepthError):
    pass


def reset_depth_cache() -> None:
    global _depth_model, _depth_model_path, _depth_load_count
    with _lock:
        _depth_model = None
        _depth_model_path = None
        _depth_load_count = 0


def get_depth_config() -> dict[str, Any]:
    import os

    def _bool(name: str, default: str) -> bool:
        return os.getenv(name, default).lower() == "true"

    def _float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            try:
                from flask import current_app

                return float(current_app.config.get(name, default))
            except Exception:
                return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            try:
                from flask import current_app

                return int(current_app.config.get(name, default))
            except Exception:
                return default
        try:
            return int(raw)
        except ValueError:
            return default

    cfg = {
        "enabled": False,
        "force_mock": True,
        "model_path": "",
        "model_type": "relative_mock",
        "device": "cpu",
        "imgsz": 384,
        "output_dir": "",
        "normalization": "relative",
        "min_confidence": 0.0,
    }
    try:
        from flask import current_app

        cfg.update(
            {
                "enabled": bool(current_app.config.get("DEPTH_ENABLED", False)),
                "force_mock": bool(current_app.config.get("DEPTH_FORCE_MOCK", True)),
                "model_path": current_app.config.get("DEPTH_MODEL_PATH") or "",
                "model_type": current_app.config.get("DEPTH_MODEL_TYPE") or "relative_mock",
                "device": current_app.config.get("DEPTH_DEVICE") or "cpu",
                "imgsz": int(current_app.config.get("DEPTH_IMAGE_SIZE", 384)),
                "output_dir": current_app.config.get("DEPTH_OUTPUT_DIR") or "",
                "normalization": current_app.config.get("DEPTH_NORMALIZATION_MODE")
                or "relative",
                "min_confidence": float(current_app.config.get("DEPTH_MIN_CONFIDENCE", 0.0)),
            }
        )
    except RuntimeError:
        pass

    if os.getenv("DEPTH_ENABLED") is not None:
        cfg["enabled"] = _bool("DEPTH_ENABLED", "false")
    if os.getenv("DEPTH_FORCE_MOCK") is not None:
        cfg["force_mock"] = _bool("DEPTH_FORCE_MOCK", "true")
    if os.getenv("DEPTH_MODEL_PATH") is not None:
        cfg["model_path"] = os.getenv("DEPTH_MODEL_PATH") or ""
    if os.getenv("DEPTH_MODEL_TYPE"):
        cfg["model_type"] = os.getenv("DEPTH_MODEL_TYPE") or "relative_mock"
    if os.getenv("DEPTH_DEVICE"):
        cfg["device"] = os.getenv("DEPTH_DEVICE") or "cpu"
    cfg["imgsz"] = _int("DEPTH_IMAGE_SIZE", cfg["imgsz"])
    if os.getenv("DEPTH_OUTPUT_DIR") is not None:
        cfg["output_dir"] = os.getenv("DEPTH_OUTPUT_DIR") or ""
    if os.getenv("DEPTH_NORMALIZATION_MODE"):
        cfg["normalization"] = os.getenv("DEPTH_NORMALIZATION_MODE") or "relative"
    cfg["min_confidence"] = _float("DEPTH_MIN_CONFIDENCE", cfg["min_confidence"])
    return cfg


def normalize_depth_map(
    raw: np.ndarray,
    *,
    treat_as_inverse: bool = True,
) -> np.ndarray:
    """
    Normalize a depth/inverse-depth map to float32 [0, 1].

    When treat_as_inverse=True (MiDaS-style), larger raw values are treated as
    closer; we keep that relative ordering after min-max normalization.
    """
    arr = np.asarray(raw, dtype=np.float32)
    if arr.size == 0:
        return arr
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros_like(arr, dtype=np.float32)
    vals = arr[finite]
    lo, hi = float(vals.min()), float(vals.max())
    if hi - lo < 1e-8:
        out = np.zeros_like(arr, dtype=np.float32)
        out[finite] = 0.5
        return out
    norm = (arr - lo) / (hi - lo)
    if not treat_as_inverse:
        # Convert "larger = farther" into "larger = closer" for consistency
        norm = 1.0 - norm
    return np.clip(norm.astype(np.float32), 0.0, 1.0)


def depth_map_stats(depth: np.ndarray) -> dict[str, float]:
    flat = np.asarray(depth, dtype=np.float32).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return {"min_depth": 0.0, "max_depth": 0.0, "mean_depth": 0.0, "median_depth": 0.0}
    return {
        "min_depth": float(np.min(flat)),
        "max_depth": float(np.max(flat)),
        "mean_depth": float(np.mean(flat)),
        "median_depth": float(np.median(flat)),
    }


def region_depth_stats(
    depth: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    bbox: list[float] | None = None,
) -> dict[str, float] | None:
    h, w = depth.shape[:2]
    if mask is not None:
        m = np.asarray(mask).astype(bool)
        if m.shape != depth.shape:
            return None
        vals = depth[m]
    elif bbox is not None and len(bbox) == 4:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, max(x1 + 1, x2)), min(h, max(y1 + 1, y2))
        vals = depth[y1:y2, x1:x2].ravel()
    else:
        return None
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return None
    return {
        "mean_relative_depth": float(np.mean(vals)),
        "median_relative_depth": float(np.median(vals)),
        "min_relative_depth": float(np.min(vals)),
        "max_relative_depth": float(np.max(vals)),
    }


def get_depth_model() -> Any:
    global _depth_model, _depth_model_path, _depth_load_count
    cfg = get_depth_config()
    if not cfg["enabled"]:
        raise DepthDisabled("Depth estimation is disabled (DEPTH_ENABLED=false).")
    if cfg["force_mock"]:
        return "mock"

    path_str = (cfg["model_path"] or "").strip()
    if not path_str:
        raise DepthModelMissing(
            "DEPTH_ENABLED=true and DEPTH_FORCE_MOCK=false but DEPTH_MODEL_PATH is empty. "
            "Provide a local depth weights file or set DEPTH_FORCE_MOCK=true. "
            "No MiDaS/torch.hub download is performed."
        )
    model_path = Path(path_str)
    if not model_path.is_absolute():
        model_path = (Path(__file__).resolve().parent.parent / model_path).resolve()
    if not model_path.is_file():
        raise DepthModelMissing(
            f"Depth weights not found at '{model_path}'. No automatic download is performed."
        )

    with _lock:
        if _depth_model is not None and _depth_model_path == str(model_path):
            return _depth_model
        # Optional local torch.load of a scripted/stateful model — no hub download.
        try:
            import torch

            loaded = torch.load(str(model_path), map_location=cfg["device"])
            if hasattr(loaded, "eval"):
                loaded.eval()
        except Exception as exc:
            raise DepthModelMissing(
                f"Failed to load depth model '{model_path}': {exc}"
            ) from exc
        _depth_model = loaded
        _depth_model_path = str(model_path)
        _depth_load_count += 1
        logger.info("Loaded depth model %s (count=%s)", model_path, _depth_load_count)
        return _depth_model


def _open_rgb(path: Path) -> tuple[np.ndarray, int, int]:
    try:
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"))
            w, h = im.size
    except Exception as exc:
        raise DepthInvalidImage(f"Invalid image for depth: {path}") from exc
    if w < 1 or h < 1:
        raise DepthInvalidImage(f"Empty image for depth: {path}")
    return rgb, w, h


def _mock_depth_map(width: int, height: int, seed: int) -> np.ndarray:
    """Deterministic relative (inverse-depth-like) map in [0, 1]."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    dist = np.sqrt(((xx - cx) / max(width, 1)) ** 2 + ((yy - cy) / max(height, 1)) ** 2)
    # Center closer (higher relative depth), edges farther — inverse-depth style
    base = 1.0 - np.clip(dist / 0.75, 0.0, 1.0)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.02, size=base.shape).astype(np.float32)
    return normalize_depth_map(base + noise, treat_as_inverse=True)


def _run_local_torch_depth(model: Any, rgb: np.ndarray, imgsz: int, device: str) -> np.ndarray:
    import torch
    import torch.nn.functional as F

    h, w = rgb.shape[:2]
    tensor = torch.from_numpy(rgb).float() / 255.0
    tensor = tensor.permute(2, 0, 1).unsqueeze(0)
    tensor = F.interpolate(tensor, size=(imgsz, imgsz), mode="bilinear", align_corners=False)
    tensor = tensor.to(device)
    with torch.no_grad():
        out = model(tensor) if callable(model) else model
        if isinstance(out, (tuple, list)):
            out = out[0]
        if hasattr(out, "prediction"):
            out = out.prediction
        arr = out.detach().float().cpu().numpy()
    arr = np.squeeze(arr)
    if arr.ndim != 2:
        raise DepthError(f"Unexpected depth output shape: {arr.shape}")
    # Resize to original
    from PIL import Image as PILImage

    resized = np.asarray(
        PILImage.fromarray(arr).resize((w, h), resample=PILImage.BILINEAR)
    )
    return normalize_depth_map(resized, treat_as_inverse=True)


def estimate_depth(
    image_path_or_file: str | Path,
    masks: list[dict[str, Any]] | None = None,
    detections: list[dict[str, Any]] | None = None,
    image_size: int | None = None,
) -> dict[str, Any]:
    """
    Estimate relative monocular depth. Never claims metric units.

    Returns framework-independent dict with relative stats and per-region features.
    """
    cfg = get_depth_config()
    if not cfg["enabled"]:
        return {
            "enabled": False,
            "available": False,
            "depth_map": None,
            "depth_map_reference": None,
            "depth_map_width": None,
            "depth_map_height": None,
            "normalization": cfg["normalization"],
            "representation": "relative_inverse_depth_style",
            "calibration_available": False,
            "metric_depth_available": False,
            "physical_volume_available": False,
            "min_depth": None,
            "max_depth": None,
            "mean_depth": None,
            "median_depth": None,
            "per_region": [],
            "model_version": None,
            "source_width": None,
            "source_height": None,
            "error": None,
        }

    path = Path(image_path_or_file)
    if not path.is_file():
        raise DepthInvalidImage(f"Image not found: {path}")

    rgb, width, height = _open_rgb(path)
    imgsz = int(image_size) if image_size is not None else int(cfg["imgsz"])
    model = get_depth_model()

    if model == "mock":
        seed = abs(hash(str(path.resolve()))) % (2**32)
        depth = _mock_depth_map(width, height, seed)
        model_version = "mock-depth"
    else:
        depth = _run_local_torch_depth(model, rgb, imgsz, cfg["device"])
        model_version = Path(cfg["model_path"]).name or "local-depth"

    stats = depth_map_stats(depth)
    per_region: list[dict[str, Any]] = []
    region_id = 0

    for mask_item in masks or []:
        m = mask_item.get("mask")
        stats_r = region_depth_stats(depth, mask=m if m is not None else None, bbox=mask_item.get("bbox"))
        if stats_r is None:
            continue
        per_region.append(
            {
                "region_id": region_id,
                "region_type": "mask",
                "mask_or_bbox_reference": mask_item.get("mask_reference")
                or mask_item.get("matched_detection_id"),
                "matched_detection_id": mask_item.get("matched_detection_id"),
                **stats_r,
            }
        )
        region_id += 1

    if not per_region:
        for idx, det in enumerate(detections or []):
            conf = float(det.get("confidence") or 0.0)
            if conf < float(cfg["min_confidence"]):
                continue
            stats_r = region_depth_stats(depth, bbox=det.get("bbox_xyxy"))
            if stats_r is None:
                continue
            per_region.append(
                {
                    "region_id": region_id,
                    "region_type": "bbox",
                    "mask_or_bbox_reference": det.get("detection_id") or idx,
                    "matched_detection_id": det.get("detection_id") or idx,
                    **stats_r,
                }
            )
            region_id += 1

    return {
        "enabled": True,
        "available": True,
        "depth_map": depth,  # strip for JSON
        "depth_map_reference": None,
        "depth_map_width": int(width),
        "depth_map_height": int(height),
        "normalization": "relative",
        "representation": "relative_inverse_depth_style",
        "calibration_available": False,
        "metric_depth_available": False,
        "physical_volume_available": False,
        "min_depth": stats["min_depth"],
        "max_depth": stats["max_depth"],
        "mean_depth": stats["mean_depth"],
        "median_depth": stats["median_depth"],
        "per_region": per_region,
        "model_version": model_version,
        "source_width": int(width),
        "source_height": int(height),
        "error": None,
    }


def serialize_depth_for_json(payload: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in payload.items() if k != "depth_map"}
    return out
