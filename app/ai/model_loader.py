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
_model_path_loaded: str | None = None  # "<path>|<mtime_ns>|<size>": a newly promoted best.pt reloads itself
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
    # <project root>/artifacts/ai_models/best.pt
    return Path(__file__).resolve().parents[2] / "artifacts" / "ai_models" / "best.pt"


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
            "imgsz": int(env_imgsz) if (env_imgsz or "").strip().isdigit() else 640,
            "device": env_device or "cpu",
            "force_mock": (env_force or "false").lower() == "true",
        }

    if env_path:
        cfg["model_path"] = env_path
    if env_conf is not None:
        cfg["confidence"] = float(env_conf)
    if env_iou is not None:
        cfg["iou"] = float(env_iou)
    imgsz_auto = env_imgsz is None or env_imgsz.strip().lower() in {"", "auto"}
    if not imgsz_auto:
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

    # Model card written by scripts/training/evaluate.py --promote: training image size
    # and per-class confidence thresholds tuned on RDD India val.
    card = load_model_card(model_path)
    cfg["model_card"] = card
    cfg["class_thresholds"] = {}
    if card:
        if imgsz_auto and card.get("imgsz"):
            cfg["imgsz"] = int(card["imgsz"])
        if (os.getenv("YOLO_CLASS_THRESHOLDS") or "on").lower() not in {"off", "false", "0"}:
            cfg["class_thresholds"] = {
                str(k): float(v) for k, v in (card.get("class_thresholds") or {}).items()
            }
    return cfg


def model_card_path(model_path: str | Path) -> Path:
    path = Path(model_path)
    return path.with_name(f"{path.stem}.model.json")


def load_model_card(model_path: str | Path) -> dict[str, Any] | None:
    """``best.pt`` -> ``best.model.json`` (None when absent or unreadable)."""
    import json

    card_path = model_card_path(model_path)
    if not card_path.is_file():
        return None
    try:
        data = json.loads(card_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("Ignoring unreadable model card %s", card_path)
        return None


class ModelUnavailableError(RuntimeError):
    """Real weights could not be loaded and the mock was not explicitly requested."""


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
    """Load YOLO weights once and reuse.

    The mock is used only when YOLO_FORCE_MOCK=true. A missing or unloadable model
    raises ModelUnavailableError instead: silently swapping in the mock stamped a
    fake 91 % pothole on every photo (and failed every repair check) when a YOLO11
    model met an Ultralytics version too old to read it. Failures are not cached,
    so fixing the weights or the environment takes effect on the next request.
    """
    global _model, _model_path_loaded, _load_count

    cfg = get_model_config()
    path = str(Path(cfg["model_path"]).resolve())
    try:
        st = Path(path).stat()
        cache_key = f"{path}|{st.st_mtime_ns}|{st.st_size}"  # copy2 keeps mtime, so size too
    except OSError:
        cache_key = f"{path}|missing"

    with _lock:
        if (
            not force_reload
            and _model is not None
            and _model_path_loaded == cache_key
        ):
            return _model

        if cfg.get("force_mock"):
            _model = MockYOLOModel()
            _model_path_loaded = cache_key
            _load_count += 1
            logger.info("Using MockYOLOModel (YOLO_FORCE_MOCK=true)")
            return _model

        weights = Path(path)
        if not weights.is_file():
            logger.error("Model weights not found at %s", path)
            raise ModelUnavailableError(f"Detection model not found at {path}.")

        try:
            import ultralytics
            from ultralytics import YOLO

            _model = YOLO(str(weights))
        except Exception as exc:
            version = getattr(locals().get("ultralytics"), "__version__", "not installed")
            logger.error("Failed to load YOLO weights %s (ultralytics %s): %s", path, version, exc)
            raise ModelUnavailableError(
                f"Could not load the detection model {weights.name} with ultralytics {version}: {exc}. "
                "YOLO11 models need ultralytics 8.3 or newer (pip install -r requirements.txt)."
            ) from exc
        _model_path_loaded = cache_key
        _load_count += 1
        logger.info(
            "Loaded YOLO weights from %s with ultralytics %s (load #%s)",
            path, ultralytics.__version__, _load_count,
        )
        return _model


def _pretty_arch(base: Any) -> str | None:
    """'yolo11m.pt' -> 'YOLO11m'."""
    if not base:
        return None
    stem = Path(str(base)).stem
    return "YOLO" + stem[4:] if stem.lower().startswith("yolo") else stem


def active_model_summary() -> dict[str, Any]:
    """What the detector page and settings show: the live model and its card."""
    cfg = get_model_config()
    card = cfg.get("model_card") or {}
    run = card.get("run_card") or {}
    val = card.get("metrics_india_val") or {}
    path = Path(cfg["model_path"])
    base = run.get("base_weights") or (run.get("train_args") or {}).get("model")
    return {
        "file": path.name,
        "exists": path.is_file(),
        "mock": bool(cfg.get("force_mock")),
        "architecture": _pretty_arch(base),
        "imgsz": cfg.get("imgsz"),
        "device": cfg.get("device"),
        "promoted_at": (card.get("promoted_at") or "").replace("T", " ") or None,
        "map50": val.get("map50"),
        "precision": val.get("precision"),
        "recall": val.get("recall"),
        "threshold_method": card.get("threshold_method"),
        "thresholds": {
            friendly_name(name).replace("_", " ").capitalize(): conf
            for name, conf in (cfg.get("class_thresholds") or {}).items()
        },
    }
