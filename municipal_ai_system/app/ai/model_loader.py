"""Singleton YOLO / mock model loader."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_model: Any = None
_model_path_loaded: str | None = None
_load_count = 0

# RDD2022 / notebook class map (fallback if weights lack names)
DEFAULT_CLASS_NAMES = {
    0: "D00_longitudinal_crack",
    1: "D10_transverse_crack",
    2: "D20_alligator_crack",
    3: "D40_pothole",
}

FRIENDLY_CLASS_NAMES = {
    "D00_longitudinal_crack": "longitudinal_crack",
    "D10_transverse_crack": "transverse_crack",
    "D20_alligator_crack": "alligator_crack",
    "D40_pothole": "pothole",
}


def default_model_path() -> Path:
    return Path(__file__).resolve().parent.parent / "ai_models" / "best.pt"


def get_model_config() -> dict[str, Any]:
    import os

    # Env vars win so offline smoke tests can flip YOLO_FORCE_MOCK without
    # rebuilding the Flask Config object.
    env_force = os.getenv("YOLO_FORCE_MOCK")
    env_path = os.getenv("YOLO_MODEL_PATH")
    env_conf = os.getenv("YOLO_CONFIDENCE")
    env_iou = os.getenv("YOLO_IOU")
    env_imgsz = os.getenv("YOLO_IMGSZ")
    env_device = os.getenv("YOLO_DEVICE")

    cfg: dict[str, Any] = {}
    try:
        cfg = {
            "model_path": current_app.config.get("YOLO_MODEL_PATH"),
            "confidence": float(current_app.config.get("YOLO_CONFIDENCE", 0.25)),
            "iou": float(current_app.config.get("YOLO_IOU", 0.45)),
            "imgsz": int(current_app.config.get("YOLO_IMGSZ", 640)),
            "device": current_app.config.get("YOLO_DEVICE", "cpu"),
            "force_mock": bool(current_app.config.get("YOLO_FORCE_MOCK", False)),
        }
    except RuntimeError:
        cfg = {
            "model_path": env_path or str(default_model_path()),
            "confidence": float(env_conf or "0.25"),
            "iou": float(env_iou or "0.45"),
            "imgsz": int(env_imgsz or "640"),
            "device": env_device or "cpu",
            "force_mock": (env_force or "false").lower() == "true",
        }

    if env_path:
        cfg["model_path"] = env_path
    if env_conf is not None:
        cfg["confidence"] = float(env_conf)
    if env_iou is not None:
        cfg["iou"] = float(env_iou)
    if env_imgsz is not None:
        cfg["imgsz"] = int(env_imgsz)
    if env_device:
        cfg["device"] = env_device
    if env_force is not None:
        cfg["force_mock"] = env_force.lower() == "true"

    if not cfg.get("model_path"):
        cfg["model_path"] = str(default_model_path())

    # Always resolve relative paths against the app package root.
    model_path = Path(cfg["model_path"])
    if not model_path.is_absolute():
        model_path = (Path(__file__).resolve().parent.parent / model_path).resolve()
    else:
        model_path = model_path.resolve()
    cfg["model_path"] = str(model_path)
    return cfg


class MockYOLOModel:
    """Deterministic offline detector used when weights/ultralytics unavailable."""

    names = DEFAULT_CLASS_NAMES

    def predict(self, source, conf=0.25, iou=0.45, imgsz=640, device="cpu", verbose=False):
        from PIL import Image

        path = Path(source)
        with Image.open(path) as image:
            width, height = image.size

        # One synthetic pothole box in the center for non-empty tests.
        cx, cy = width / 2, height / 2
        bw, bh = max(width * 0.2, 40), max(height * 0.15, 30)
        x1, y1 = cx - bw / 2, cy - bh / 2
        x2, y2 = cx + bw / 2, cy + bh / 2

        class _Box:
            def __init__(self):
                self.xyxy = [[x1, y1, x2, y2]]
                self.conf = [0.91]
                self.cls = [3]

        class _Result:
            def __init__(self):
                self.boxes = _Box()
                self.orig_shape = (height, width)

        return [_Result()]


def load_count() -> int:
    return _load_count


def reset_model_cache() -> None:
    global _model, _model_path_loaded, _load_count
    with _lock:
        _model = None
        _model_path_loaded = None
        _load_count = 0


def get_class_names(model: Any | None = None) -> dict[int, str]:
    if model is not None:
        names = getattr(model, "names", None)
        if isinstance(names, dict) and names:
            return {int(k): str(v) for k, v in names.items()}
        if isinstance(names, (list, tuple)) and names:
            return {i: str(n) for i, n in enumerate(names)}
    return dict(DEFAULT_CLASS_NAMES)


def friendly_name(class_name: str) -> str:
    return FRIENDLY_CLASS_NAMES.get(class_name, class_name)


def get_model(force_reload: bool = False) -> Any:
    """Load YOLO weights once and reuse. Falls back to MockYOLOModel."""
    global _model, _model_path_loaded, _load_count

    cfg = get_model_config()
    path = str(Path(cfg["model_path"]).resolve())

    with _lock:
        if (
            not force_reload
            and _model is not None
            and _model_path_loaded == path
        ):
            return _model

        if cfg.get("force_mock"):
            _model = MockYOLOModel()
            _model_path_loaded = path
            _load_count += 1
            logger.info("Using MockYOLOModel (YOLO_FORCE_MOCK=true)")
            return _model

        weights = Path(path)
        if not weights.is_file():
            logger.warning(
                "Model weights not found at %s — using MockYOLOModel",
                path,
            )
            _model = MockYOLOModel()
            _model_path_loaded = path
            _load_count += 1
            return _model

        try:
            from ultralytics import YOLO

            _model = YOLO(str(weights))
            _model_path_loaded = path
            _load_count += 1
            logger.info("Loaded YOLO weights from %s (load #%s)", path, _load_count)
            return _model
        except Exception as exc:
            logger.warning(
                "Failed to load YOLO (%s) — using MockYOLOModel: %s",
                path,
                exc,
            )
            _model = MockYOLOModel()
            _model_path_loaded = path
            _load_count += 1
            return _model
