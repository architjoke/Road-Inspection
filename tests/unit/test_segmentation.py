"""Phase 8 segmentation offline tests (no RDS/internet/GPU)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.ai.image_annotator import annotate_segmentation
from app.ai.mask_processing import (
    damaged_area_percentage,
    union_mask_pixel_count,
)
from app.ai.segmentation import (
    SegmentationError,
    SegmentationModelMissing,
    assert_segmentation_capable,
    get_segmentation_config,
    reset_segmentation_cache,
    segment,
    serialize_segmentation_for_json,
)


def test_segmentation_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SEGMENTATION_ENABLED", raising=False)
    monkeypatch.setenv("SEGMENTATION_ENABLED", "false")
    cfg = get_segmentation_config()
    assert cfg["enabled"] is False


def test_segmentation_env_overrides(monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_CONFIDENCE_THRESHOLD", "0.4")
    monkeypatch.setenv("SEGMENTATION_IMAGE_SIZE", "512")
    cfg = get_segmentation_config()
    assert cfg["enabled"] is True
    assert cfg["force_mock"] is True
    assert cfg["confidence"] == 0.4
    assert cfg["imgsz"] == 512


def test_detection_only_best_pt_rejected(monkeypatch):
    YOLO = pytest.importorskip("ultralytics").YOLO

    from app.ai.model_loader import default_model_path

    weights = default_model_path()
    if not weights.is_file():
        pytest.skip("best.pt missing")
    model = YOLO(str(weights))
    with pytest.raises(SegmentationError, match="detect|Detection-only|cannot"):
        assert_segmentation_capable(model, weights)


def test_mock_segmentation_works(tmp_image, monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    detections = [
        {
            "class_id": 3,
            "class_name": "D40_pothole",
            "damage_type": "pothole",
            "confidence": 0.9,
            "bbox_xyxy": [100, 100, 400, 300],
        }
    ]
    payload = segment(tmp_image, detections=detections)
    assert payload["enabled"] is True
    assert payload["available"] is True
    assert payload["results"]
    row = payload["results"][0]
    assert row["mask_pixel_count"] > 0
    assert row["damaged_area_percentage"] > 0
    assert row["physical_area_available"] is False
    assert row["polygon"]


def test_missing_real_segmentation_model(monkeypatch, tmp_image):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "false")
    monkeypatch.setenv("SEGMENTATION_MODEL_PATH", "artifacts/ai_models/missing_seg.pt")
    reset_segmentation_cache()
    with pytest.raises(SegmentationModelMissing):
        segment(tmp_image)


def test_empty_mask_and_union(tmp_path):
    h, w = 100, 100
    a = np.zeros((h, w), dtype=np.uint8)
    b = np.zeros((h, w), dtype=np.uint8)
    a[10:40, 10:40] = 1
    b[30:60, 30:60] = 1
    union = union_mask_pixel_count([a, b], h, w)
    assert union == int(np.count_nonzero(a | b))
    mask_pixel_count_sum = int(a.sum()) + int(b.sum())
    assert union < mask_pixel_count_sum
    assert damaged_area_percentage(0, w, h) == 0.0
    empty = np.zeros((h, w), dtype=np.uint8)
    assert union_mask_pixel_count([empty], h, w) == 0


def test_multiple_masks_and_matching(tmp_image, monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    detections = [
        {
            "class_id": 0,
            "class_name": "D00_longitudinal_crack",
            "confidence": 0.8,
            "bbox_xyxy": [50, 50, 200, 150],
            "detection_id": 11,
        },
        {
            "class_id": 3,
            "class_name": "D40_pothole",
            "confidence": 0.85,
            "bbox_xyxy": [400, 300, 700, 500],
            "detection_id": 12,
        },
    ]
    payload = segment(tmp_image, detections=detections)
    assert len(payload["results"]) >= 2
    matched = [r["matched_detection_id"] for r in payload["results"]]
    assert any(m is not None for m in matched)
    assert payload["union_damaged_area_percentage"] >= max(
        r["damaged_area_percentage"] for r in payload["results"]
    ) - 1e-6


def test_no_yolo_detections_mock_still_returns_mask(tmp_image, monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    payload = segment(tmp_image, detections=[])
    assert payload["results"]


def test_invalid_image_segmentation(tmp_path, monkeypatch, corrupted_image):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    with pytest.raises(Exception):
        segment(corrupted_image)


def test_mask_annotation_does_not_overwrite(tmp_image, monkeypatch, tmp_path):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    payload = segment(tmp_image, detections=[])
    original = tmp_image.read_bytes()
    out = tmp_path / "seg_overlay.jpg"
    annotate_segmentation(tmp_image, payload["results"], out)
    assert tmp_image.read_bytes() == original
    assert out.is_file()
    assert out.resolve() != tmp_image.resolve()


def test_segmentation_failure_preserves_detections(monkeypatch, tmp_image):
    from app.workers.inspection_worker import _run_optional_segmentation

    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "false")
    monkeypatch.setenv("SEGMENTATION_MODEL_PATH", "")
    reset_segmentation_cache()
    detections = [
        {
            "class_id": 1,
            "class_name": "D10_transverse_crack",
            "confidence": 0.7,
            "bbox_xyxy": [10, 10, 50, 50],
            "severity": "MINOR",
        }
    ]
    section, kept, raw = _run_optional_segmentation(tmp_image, detections)
    assert kept == detections
    assert section.get("error")
    assert raw == []


def test_serialize_strips_numpy_masks(tmp_image, monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    reset_segmentation_cache()
    payload = segment(tmp_image, detections=[])
    serialized = serialize_segmentation_for_json(payload)
    assert all("mask" not in r for r in serialized["results"])
