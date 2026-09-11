"""Phase 8 pixel-level damage segmentation (optional; mock-first)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.ai.mask_processing import (
    associate_masks_with_detections,
    damaged_area_percentage,
    mask_pixel_count,
    mask_to_bbox,
    mask_to_polygon,
    polygon_normalized,
    resize_mask,
    union_mask_pixel_count,
)

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_seg_model: Any = None
_seg_model_path: str | None = None
_seg_load_count = 0


class SegmentationError(Exception):
    """Controlled segmentation failure."""


class SegmentationDisabled(SegmentationError):
    pass


class SegmentationModelMissing(SegmentationError):
    pass


class SegmentationInvalidImage(SegmentationError):
    pass


def reset_segmentation_cache() -> None:
    global _seg_model, _seg_model_path, _seg_load_count
    with _lock:
        _seg_model = None
        _seg_model_path = None
        _seg_load_count = 0


def get_segmentation_config() -> dict[str, Any]:
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
        "device": "cpu",
        "confidence": 0.25,
        "imgsz": 640,
        "output_dir": "",
    }
    try:
        from flask import current_app

        cfg.update(
            {
                "enabled": bool(current_app.config.get("SEGMENTATION_ENABLED", False)),
                "force_mock": bool(current_app.config.get("SEGMENTATION_FORCE_MOCK", True)),
                "model_path": current_app.config.get("SEGMENTATION_MODEL_PATH") or "",
                "device": current_app.config.get("SEGMENTATION_DEVICE") or "cpu",
                "confidence": float(
                    current_app.config.get("SEGMENTATION_CONFIDENCE_THRESHOLD", 0.25)
                ),
                "imgsz": int(current_app.config.get("SEGMENTATION_IMAGE_SIZE", 640)),
                "output_dir": current_app.config.get("SEGMENTATION_OUTPUT_DIR") or "",
            }
        )
    except RuntimeError:
        pass

    if os.getenv("SEGMENTATION_ENABLED") is not None:
        cfg["enabled"] = _bool("SEGMENTATION_ENABLED", "false")
    if os.getenv("SEGMENTATION_FORCE_MOCK") is not None:
        cfg["force_mock"] = _bool("SEGMENTATION_FORCE_MOCK", "true")
    if os.getenv("SEGMENTATION_MODEL_PATH") is not None:
        cfg["model_path"] = os.getenv("SEGMENTATION_MODEL_PATH") or ""
    if os.getenv("SEGMENTATION_DEVICE"):
        cfg["device"] = os.getenv("SEGMENTATION_DEVICE") or "cpu"
    cfg["confidence"] = _float("SEGMENTATION_CONFIDENCE_THRESHOLD", cfg["confidence"])
    cfg["imgsz"] = _int("SEGMENTATION_IMAGE_SIZE", cfg["imgsz"])
    if os.getenv("SEGMENTATION_OUTPUT_DIR") is not None:
        cfg["output_dir"] = os.getenv("SEGMENTATION_OUTPUT_DIR") or ""
    return cfg


def assert_segmentation_capable(model: Any, model_path: str | Path) -> None:
    """Reject detection-only checkpoints (e.g. best.pt)."""
    path = Path(model_path)
    task = getattr(model, "task", None)
    if task is None and hasattr(model, "model"):
        task = getattr(model.model, "task", None)
    if task and str(task).lower() not in {"segment", "seg"}:
        raise SegmentationError(
            f"Segmentation model at '{path}' has task='{task}'. "
            "Detection-only weights (e.g. app/ai_models/best.pt) cannot produce masks. "
            "Provide a YOLO-seg checkpoint or enable SEGMENTATION_FORCE_MOCK=true."
        )
    # Ultralytics: check for masks attribute expectation via overrides
    names = getattr(model, "names", None)
    if task is None and path.name.lower() == "best.pt":
        raise SegmentationError(
            "app/ai_models/best.pt is detection-only and cannot be used for segmentation."
        )
    _ = names  # quiet lint


def _load_yolo_seg(model_path: Path, device: str) -> Any:
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    assert_segmentation_capable(model, model_path)
    # Warm device preference without running predict yet
    try:
        model.to(device)
    except Exception:
        logger.debug("Could not move seg model to %s; will use predict(device=...)", device)
    return model


def get_segmentation_model() -> Any:
    """Lazy singleton. Returns 'mock' sentinel when force_mock."""
    global _seg_model, _seg_model_path, _seg_load_count
    cfg = get_segmentation_config()
    if not cfg["enabled"]:
        raise SegmentationDisabled("Segmentation is disabled (SEGMENTATION_ENABLED=false).")

    if cfg["force_mock"]:
        return "mock"

    path_str = (cfg["model_path"] or "").strip()
    if not path_str:
        raise SegmentationModelMissing(
            "SEGMENTATION_ENABLED=true and SEGMENTATION_FORCE_MOCK=false but "
            "SEGMENTATION_MODEL_PATH is empty. Place a local YOLO-seg .pt file "
            "or set SEGMENTATION_FORCE_MOCK=true. No weights are downloaded."
        )
    model_path = Path(path_str)
    if not model_path.is_absolute():
        model_path = (Path(__file__).resolve().parent.parent / model_path).resolve()
    if not model_path.is_file():
        raise SegmentationModelMissing(
            f"Segmentation weights not found at '{model_path}'. "
            "No automatic download is performed."
        )

    with _lock:
        if _seg_model is not None and _seg_model_path == str(model_path):
            return _seg_model
        try:
            loaded = _load_yolo_seg(model_path, cfg["device"])
        except SegmentationError:
            raise
        except Exception as exc:
            raise SegmentationModelMissing(
                f"Failed to load segmentation model '{model_path}': {exc}"
            ) from exc
        _seg_model = loaded
        _seg_model_path = str(model_path)
        _seg_load_count += 1
        logger.info("Loaded segmentation model %s (count=%s)", model_path, _seg_load_count)
        return _seg_model


def _open_image(path: Path) -> tuple[Image.Image, int, int]:
    try:
        image = Image.open(path).convert("RGB")
    except Exception as exc:
        raise SegmentationInvalidImage(f"Invalid image for segmentation: {path}") from exc
    width, height = image.size
    if width < 1 or height < 1:
        raise SegmentationInvalidImage(f"Empty image dimensions: {path}")
    return image, width, height


def _ellipse_mask(h: int, w: int, bbox: list[float], rng: np.random.Generator) -> np.ndarray:
    import cv2

    mask = np.zeros((h, w), dtype=np.uint8)
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return mask
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    axes = (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2))
    cv2.ellipse(mask, (cx, cy), axes, 0, 0, 360, 1, -1)
    # Deterministic speckles for mock uniqueness without randomness from time
    _ = rng
    return mask


def _mock_segment(
    image_path: Path,
    detections: list[dict[str, Any]] | None,
    confidence_threshold: float,
) -> list[dict[str, Any]]:
    _, width, height = _open_image(image_path)
    rng = np.random.default_rng(abs(hash(str(image_path.resolve()))) % (2**32))
    results: list[dict[str, Any]] = []

    dets = detections or []
    if dets:
        for det in dets:
            conf = float(det.get("confidence") or 0.0)
            if conf < confidence_threshold:
                continue
            bbox = [float(v) for v in det.get("bbox_xyxy") or [0, 0, 0, 0]]
            mask = _ellipse_mask(height, width, bbox, rng)
            pixels = mask_pixel_count(mask)
            if pixels == 0:
                continue
            polygon = mask_to_polygon(mask)
            results.append(
                _normalize_mask_result(
                    class_id=det.get("class_id"),
                    class_name=det.get("class_name") or det.get("damage_type") or "damage",
                    confidence=conf,
                    mask=mask,
                    polygon=polygon,
                    bbox=mask_to_bbox(mask) or bbox,
                    width=width,
                    height=height,
                    model_version="mock-segmentation",
                )
            )
    else:
        # Synthetic center blob when no detections
        cx, cy = width // 2, height // 2
        bw, bh = max(8, width // 8), max(8, height // 8)
        bbox = [cx - bw, cy - bh, cx + bw, cy + bh]
        mask = _ellipse_mask(height, width, bbox, rng)
        polygon = mask_to_polygon(mask)
        results.append(
            _normalize_mask_result(
                class_id=3,
                class_name="D40_pothole",
                confidence=0.5,
                mask=mask,
                polygon=polygon,
                bbox=mask_to_bbox(mask) or bbox,
                width=width,
                height=height,
                model_version="mock-segmentation",
            )
        )
    return associate_masks_with_detections(results, dets)


def _normalize_mask_result(
    *,
    class_id: Any,
    class_name: str,
    confidence: float,
    mask: np.ndarray,
    polygon: list[list[float]],
    bbox: list[float],
    width: int,
    height: int,
    model_version: str,
    mask_reference: str | None = None,
) -> dict[str, Any]:
    pixels = mask_pixel_count(mask)
    return {
        "class_id": class_id,
        "class_name": class_name,
        "damage_type": class_name,
        "confidence": float(confidence),
        "mask": mask.astype(np.uint8),  # in-memory only; strip before JSON persist
        "mask_reference": mask_reference,
        "polygon": polygon,
        "polygon_normalized": polygon_normalized(polygon, width, height) if polygon else [],
        "bbox": [float(v) for v in bbox],
        "mask_width": int(width),
        "mask_height": int(height),
        "mask_pixel_count": pixels,
        "damaged_area_percentage": damaged_area_percentage(pixels, width, height),
        "area_unit": "pixels",
        "physical_area_available": False,
        "model_version": model_version,
        "source_width": int(width),
        "source_height": int(height),
        "matched_detection_id": None,
    }


def _real_yolo_seg(
    model: Any,
    image_path: Path,
    detections: list[dict[str, Any]] | None,
    confidence_threshold: float,
    imgsz: int,
    device: str,
) -> list[dict[str, Any]]:
    _, width, height = _open_image(image_path)
    preds = model.predict(
        source=str(image_path),
        conf=confidence_threshold,
        imgsz=imgsz,
        device=device,
        verbose=False,
    )
    results: list[dict[str, Any]] = []
    if not preds:
        return associate_masks_with_detections(results, detections or [])

    pred = preds[0]
    names = getattr(pred, "names", None) or getattr(model, "names", {}) or {}
    masks_obj = getattr(pred, "masks", None)
    boxes = getattr(pred, "boxes", None)
    if masks_obj is None or getattr(masks_obj, "data", None) is None:
        return associate_masks_with_detections(results, detections or [])

    mask_data = masks_obj.data.detach().cpu().numpy()
    for i in range(mask_data.shape[0]):
        raw = mask_data[i]
        mask = resize_mask(raw, width, height)
        pixels = mask_pixel_count(mask)
        if pixels == 0:
            continue
        class_id = None
        confidence = 0.0
        if boxes is not None and i < len(boxes):
            try:
                class_id = int(boxes.cls[i].item())
                confidence = float(boxes.conf[i].item())
            except Exception:
                pass
        class_name = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
        bbox = mask_to_bbox(mask) or [0.0, 0.0, 0.0, 0.0]
        polygon = mask_to_polygon(mask)
        results.append(
            _normalize_mask_result(
                class_id=class_id,
                class_name=class_name,
                confidence=confidence,
                mask=mask,
                polygon=polygon,
                bbox=bbox,
                width=width,
                height=height,
                model_version=Path(str(getattr(model, "ckpt_path", "") or "yolo-seg")).name
                or "yolo-seg",
            )
        )
    return associate_masks_with_detections(results, detections or [])


def segment(
    image_path_or_file: str | Path,
    detections: list[dict[str, Any]] | None = None,
    confidence_threshold: float | None = None,
    image_size: int | None = None,
) -> dict[str, Any]:
    """
    Run optional segmentation and return framework-independent payload.

    Returns:
      {
        enabled, available, results, union_mask_pixel_count,
        union_damaged_area_percentage, model_version, error?
      }
    """
    cfg = get_segmentation_config()
    if not cfg["enabled"]:
        return {
            "enabled": False,
            "available": False,
            "results": [],
            "union_mask_pixel_count": 0,
            "union_damaged_area_percentage": 0.0,
            "model_version": None,
            "error": None,
        }

    path = Path(image_path_or_file)
    if not path.is_file():
        raise SegmentationInvalidImage(f"Image not found: {path}")

    conf = (
        float(confidence_threshold)
        if confidence_threshold is not None
        else float(cfg["confidence"])
    )
    imgsz = int(image_size) if image_size is not None else int(cfg["imgsz"])

    model = get_segmentation_model()
    if model == "mock":
        results = _mock_segment(path, detections, conf)
        model_version = "mock-segmentation"
    else:
        results = _real_yolo_seg(
            model, path, detections, conf, imgsz, cfg["device"]
        )
        model_version = Path(cfg["model_path"]).name or "yolo-seg"

    height = results[0]["source_height"] if results else 0
    width = results[0]["source_width"] if results else 0
    if not results:
        with Image.open(path) as im:
            width, height = im.size

    masks = [r["mask"] for r in results if r.get("mask") is not None]
    union_pixels = union_mask_pixel_count(masks, height, width) if height and width else 0

    return {
        "enabled": True,
        "available": True,
        "results": results,
        "union_mask_pixel_count": union_pixels,
        "union_damaged_area_percentage": damaged_area_percentage(union_pixels, width, height)
        if width and height
        else 0.0,
        "model_version": model_version,
        "source_width": width,
        "source_height": height,
        "error": None,
    }


def serialize_segmentation_for_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip in-memory numpy masks for task/API JSON."""
    out = dict(payload)
    serialized = []
    for item in payload.get("results") or []:
        row = {k: v for k, v in item.items() if k != "mask"}
        serialized.append(row)
    out["results"] = serialized
    return out
