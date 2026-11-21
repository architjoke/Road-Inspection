"""Real-model CPU smoke tests (no download, no GPU, no RDS)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from app.ai.image_annotator import annotate_detections
from app.ai.model_loader import (
    DEFAULT_CLASS_NAMES,
    default_model_path,
    get_class_names,
    get_model,
    get_model_config,
    reset_model_cache,
)
from app.ai.yolo_inference import detect_image, normalize_detections


pytestmark = pytest.mark.real_model


@pytest.fixture
def real_model_env(monkeypatch):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "false")
    monkeypatch.setenv("YOLO_DEVICE", "cpu")
    monkeypatch.delenv("YOLO_MODEL_PATH", raising=False)
    reset_model_cache()
    weights = default_model_path()
    if not weights.is_file():
        pytest.skip(f"Weights not found at {weights}")
    yield weights
    reset_model_cache()


def test_weights_path_is_relative_configured(real_model_env):
    cfg = get_model_config()
    path = Path(cfg["model_path"])
    assert path.is_file()
    assert path.name == "best.pt"
    assert "ai_models" in path.parts
    assert cfg["force_mock"] is False
    assert cfg["device"] == "cpu"


def test_real_model_loads_once_on_cpu(real_model_env):
    model = get_model()
    again = get_model()
    assert model is again
    names = get_class_names(model)
    assert names
    # Trained metadata should align with project class IDs 0-3 when present
    for class_id in DEFAULT_CLASS_NAMES:
        if class_id in names:
            assert isinstance(names[class_id], str)
            assert names[class_id]


def test_real_cpu_inference_and_empty_safe(real_model_env, tmp_path):
    image = tmp_path / "blank_road.jpg"
    Image.new("RGB", (640, 640), color=(90, 90, 90)).save(image, format="JPEG")

    cfg = get_model_config()
    dets = detect_image(
        image,
        confidence=cfg["confidence"],
        iou=cfg["iou"],
        imgsz=min(cfg["imgsz"], 320),  # faster smoke
        device="cpu",
    )
    assert isinstance(dets, list)
    # Empty or non-empty both OK — must not raise
    for det in dets:
        assert "class_id" in det
        assert "confidence" in det
        assert len(det["bbox_xyxy"]) == 4

    empty = normalize_detections(
        [],
        width=640,
        height=640,
        model_version="best.pt",
        confidence_threshold=0.99,
    )
    assert empty == []


def test_real_annotate_does_not_overwrite(real_model_env, tmp_path):
    image = tmp_path / "source.jpg"
    Image.new("RGB", (512, 512), color=(40, 40, 40)).save(image, format="JPEG")
    original = image.read_bytes()
    dets = detect_image(image, imgsz=320, device="cpu")
    out = tmp_path / "source_annotated.jpg"
    annotate_detections(image, dets, out)
    assert image.read_bytes() == original
    assert out.exists()
    assert out.resolve() != image.resolve()
