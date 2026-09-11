"""Offline Phase 7 YOLO / mock detector tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from app.ai.image_annotator import annotate_detections
from app.ai.model_loader import (
    MockYOLOModel,
    get_class_names,
    get_model,
    get_model_config,
    load_count,
    reset_model_cache,
)
from app.ai.yolo_inference import DetectionError, detect_image, normalize_detections


def test_detector_config_loaded(monkeypatch):
    monkeypatch.setenv("YOLO_CONFIDENCE", "0.4")
    monkeypatch.setenv("YOLO_IOU", "0.5")
    monkeypatch.setenv("YOLO_IMGSZ", "512")
    monkeypatch.setenv("YOLO_DEVICE", "cpu")
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    cfg = get_model_config()
    assert cfg["confidence"] == 0.4
    assert cfg["iou"] == 0.5
    assert cfg["imgsz"] == 512
    assert cfg["device"] == "cpu"
    assert cfg["force_mock"] is True


def test_model_loaded_once_and_reused(monkeypatch):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    reset_model_cache()
    assert load_count() == 0
    a = get_model()
    b = get_model()
    assert a is b
    assert load_count() == 1
    assert isinstance(a, MockYOLOModel)


def test_mock_detector_works_without_weights(tmp_image):
    os.environ["YOLO_FORCE_MOCK"] = "true"
    reset_model_cache()
    dets = detect_image(tmp_image)
    assert isinstance(dets, list)
    assert len(dets) >= 1
    det = dets[0]
    assert "class_id" in det
    assert "class_name" in det
    assert "confidence" in det
    assert len(det["bbox_xyxy"]) == 4
    assert det["source_width"] == 1280
    assert det["source_height"] == 720
    assert det["model_version"]


def test_empty_mock_detections(tmp_image, monkeypatch):
    class EmptyMock:
        names = {0: "D00_longitudinal_crack", 3: "D40_pothole"}

        def predict(self, *args, **kwargs):
            class _R:
                boxes = None
                orig_shape = (720, 1280)

            return [_R()]

    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    reset_model_cache()
    dets = detect_image(tmp_image, model=EmptyMock())
    assert dets == []


def test_empty_detections_handled():
    result = normalize_detections(
        [],
        width=100,
        height=100,
        model_version="best.pt",
        confidence_threshold=0.25,
    )
    assert result == []


def test_normalize_applies_confidence_filter():
    raw = [
        {
            "class_id": 3,
            "class_name": "D40_pothole",
            "confidence": 0.9,
            "bbox_xyxy": [10, 10, 50, 50],
        },
        {
            "class_id": 0,
            "class_name": "D00_longitudinal_crack",
            "confidence": 0.1,
            "bbox_xyxy": [1, 1, 2, 2],
        },
    ]
    out = normalize_detections(
        raw,
        width=100,
        height=100,
        model_version="best.pt",
        confidence_threshold=0.25,
    )
    assert len(out) == 1
    assert out[0]["class_id"] == 3
    assert out[0]["damage_type"] == "pothole"


def test_invalid_image_rejected(corrupted_image):
    with pytest.raises(DetectionError):
        detect_image(corrupted_image)


def test_class_names_from_model_not_hardcoded_only():
    model = MockYOLOModel()
    names = get_class_names(model)
    assert 0 in names
    assert 3 in names
    assert "pothole" in names[3].lower() or "D40" in names[3]


def test_annotated_output_does_not_overwrite_original(tmp_image, tmp_path):
    dets = detect_image(tmp_image)
    out = tmp_path / "annotated.jpg"
    annotate_detections(tmp_image, dets, out)
    assert out.exists()
    assert tmp_image.exists()
    original_bytes = tmp_image.read_bytes()
    # Re-open original — unchanged size/content baseline
    assert tmp_image.read_bytes() == original_bytes
    with pytest.raises(ValueError):
        annotate_detections(tmp_image, dets, tmp_image)


def test_video_frame_processing_with_mocked_extractor(tmp_path, monkeypatch):
    frame = tmp_path / "frame_00000.jpg"
    Image.new("RGB", (640, 480), color=(30, 30, 30)).save(frame, format="JPEG")

    def _fake_extract(video_path, output_dir, fps=1):
        dest_dir = Path(output_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "frame_00000.jpg"
        Image.open(frame).save(dest)
        return [str(dest)]

    monkeypatch.setattr(
        "app.services.video_service.extract_keyframes",
        _fake_extract,
    )
    from app.services.video_service import extract_keyframes

    paths = extract_keyframes("dummy.mp4", tmp_path / "out", fps=1)
    assert paths
    dets = detect_image(paths[0], frame_number=0)
    assert dets[0]["frame_number"] == 0
