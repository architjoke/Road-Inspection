"""Phase 7 damage detection inference (Ultralytics YOLO + mock)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.utils.image_orientation import upright_size

from app.ai.model_loader import (
    friendly_name,
    get_class_names,
    get_model,
    get_model_config,
)

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class DetectionError(ValueError):
    """Invalid or unreadable media for inference."""


def _validate_image(path: Path) -> tuple[int, int]:
    if not path.exists() or not path.is_file():
        raise DetectionError(f"Image not found: {path}")
    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise DetectionError(
            f"Unsupported image type '{path.suffix}'. "
            f"Allowed: {sorted(SUPPORTED_IMAGE_SUFFIXES)}"
        )
    try:
        with Image.open(path) as image:
            image.verify()
        # Upright (EXIF-applied) size — matches the frame OpenCV/YOLO predicts on.
        width, height = upright_size(path)
        if width < 1 or height < 1:
            raise DetectionError("Image has invalid dimensions.")
        return width, height
    except UnidentifiedImageError as exc:
        raise DetectionError(f"Corrupted or unreadable image: {path}") from exc
    except OSError as exc:
        raise DetectionError(f"Unable to read image: {path}") from exc


def _to_float(value: Any) -> float:
    try:
        return float(value.item()) if hasattr(value, "item") else float(value)
    except Exception:
        return float(value)


def normalize_detections(
    raw_boxes: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    model_version: str,
    confidence_threshold: float,
    frame_number: int | None = None,
    class_thresholds: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Filter and normalize detection dicts (per-class thresholds win when given)."""
    normalized: list[dict[str, Any]] = []
    thresholds = class_thresholds or {}
    for item in raw_boxes:
        conf = float(item.get("confidence", 0))
        class_id = int(item["class_id"])
        class_name = str(item.get("class_name") or class_id)
        if conf < float(thresholds.get(class_name, confidence_threshold)):
            continue
        x1, y1, x2, y2 = item["bbox_xyxy"]
        entry = {
            "class_id": class_id,
            "class_name": class_name,
            "damage_type": friendly_name(class_name),
            "confidence": round(conf, 4),
            "bbox_xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
            "bbox_normalized": [
                round(x1 / width, 6) if width else 0,
                round(y1 / height, 6) if height else 0,
                round(x2 / width, 6) if width else 0,
                round(y2 / height, 6) if height else 0,
            ],
            "source_width": width,
            "source_height": height,
            "model_version": model_version,
            "frame_number": frame_number,
        }
        normalized.append(entry)
    return normalized


def run_inference(
    image_path: str | Path,
    *,
    confidence: float | None = None,
    iou: float | None = None,
    imgsz: int | None = None,
    device: str | None = None,
    frame_number: int | None = None,
    model: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Run detection on a single image.

    Returns a list of normalized detection dicts (possibly empty).
    """
    path = Path(image_path)
    width, height = _validate_image(path)
    cfg = get_model_config()
    conf_thr = float(confidence if confidence is not None else cfg["confidence"])
    # An explicit ``confidence`` argument means "use exactly this"; otherwise the
    # model card's per-class thresholds (tuned on India val) apply.
    class_thresholds = {} if confidence is not None else dict(cfg.get("class_thresholds") or {})
    predict_conf = min([conf_thr, *class_thresholds.values()])
    iou_thr = float(iou if iou is not None else cfg["iou"])
    size = int(imgsz if imgsz is not None else cfg["imgsz"])
    device_name = device if device is not None else cfg["device"]

    detector = model if model is not None else get_model()
    class_names = get_class_names(detector)
    model_version = Path(str(cfg["model_path"])).name

    results = detector.predict(
        source=str(path),
        conf=predict_conf,
        iou=iou_thr,
        imgsz=size,
        device=device_name,
        verbose=False,
    )

    raw: list[dict[str, Any]] = []
    if not results:
        return []

    result = results[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []

    # Ultralytics Boxes or Mock _Box
    xyxy = getattr(boxes, "xyxy", None)
    confs = getattr(boxes, "conf", None)
    clss = getattr(boxes, "cls", None)

    if xyxy is None:
        return []

    # Tensor or list
    try:
        n = len(xyxy)
    except TypeError:
        n = int(getattr(xyxy, "shape", [0])[0]) if hasattr(xyxy, "shape") else 0

    for i in range(n):
        try:
            row = xyxy[i]
            if hasattr(row, "tolist"):
                coords = row.tolist()
            else:
                coords = list(row)
            if len(coords) == 1 and hasattr(coords[0], "__iter__"):
                coords = list(coords[0])
            conf_val = _to_float(confs[i]) if confs is not None else 0.0
            cls_val = int(_to_float(clss[i])) if clss is not None else -1
        except Exception:
            continue

        name = class_names.get(cls_val, str(cls_val))
        raw.append(
            {
                "class_id": cls_val,
                "class_name": name,
                "confidence": conf_val,
                "bbox_xyxy": [float(c) for c in coords[:4]],
            }
        )

    return normalize_detections(
        raw,
        width=width,
        height=height,
        model_version=model_version,
        confidence_threshold=conf_thr,
        frame_number=frame_number,
        class_thresholds=class_thresholds,
    )


def detect_image(image_path: str | Path, **kwargs) -> list[dict[str, Any]]:
    """Public alias used by workers/services."""
    return run_inference(image_path, **kwargs)
