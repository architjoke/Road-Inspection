"""Phase 9 depth estimation offline tests (no RDS/internet/GPU)."""

from __future__ import annotations

import numpy as np
import pytest

from app.ai.depth_estimation import (
    DepthModelMissing,
    estimate_depth,
    get_depth_config,
    normalize_depth_map,
    reset_depth_cache,
    serialize_depth_for_json,
)
from app.ai.image_annotator import annotate_depth_map
from app.ai.segmentation import reset_segmentation_cache, segment


def test_depth_disabled_by_default(monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "false")
    cfg = get_depth_config()
    assert cfg["enabled"] is False


def test_depth_env_overrides(monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    monkeypatch.setenv("DEPTH_IMAGE_SIZE", "256")
    monkeypatch.setenv("DEPTH_NORMALIZATION_MODE", "relative")
    cfg = get_depth_config()
    assert cfg["enabled"] is True
    assert cfg["imgsz"] == 256
    assert cfg["normalization"] == "relative"


def test_mock_depth_works(tmp_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    payload = estimate_depth(tmp_image, detections=[])
    assert payload["enabled"] is True
    assert payload["available"] is True
    assert payload["normalization"] == "relative"
    assert payload["calibration_available"] is False
    assert payload["metric_depth_available"] is False
    assert payload["physical_volume_available"] is False
    assert payload["depth_map"] is not None
    assert 0.0 <= payload["min_depth"] <= payload["max_depth"] <= 1.0


def test_missing_real_depth_model(tmp_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "false")
    monkeypatch.setenv("DEPTH_MODEL_PATH", "artifacts/ai_models/missing_depth.pt")
    reset_depth_cache()
    with pytest.raises(DepthModelMissing):
        estimate_depth(tmp_image)


def test_depth_map_normalization_inverse_style():
    raw = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    norm = normalize_depth_map(raw, treat_as_inverse=True)
    assert norm.min() >= 0.0 and norm.max() <= 1.0
    # larger raw stays relatively larger after min-max
    assert norm[1, 1] > norm[0, 0]


def test_relative_depth_metadata(tmp_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    payload = estimate_depth(tmp_image)
    assert "relative" in payload["normalization"]
    assert payload["metric_depth_available"] is False
    assert "inverse" in payload["representation"]


def test_per_detection_and_mask_depth_stats(tmp_image, monkeypatch):
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_segmentation_cache()
    reset_depth_cache()
    detections = [
        {
            "class_id": 3,
            "class_name": "D40_pothole",
            "confidence": 0.9,
            "bbox_xyxy": [100, 80, 400, 320],
            "detection_id": 7,
        }
    ]
    seg = segment(tmp_image, detections=detections)
    depth = estimate_depth(tmp_image, masks=seg["results"], detections=detections)
    assert depth["per_region"]
    region = depth["per_region"][0]
    assert "mean_relative_depth" in region
    assert "median_relative_depth" in region


def test_empty_mask_depth_falls_back_to_bbox(tmp_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    detections = [
        {
            "confidence": 0.9,
            "bbox_xyxy": [20, 20, 200, 200],
            "detection_id": 1,
        }
    ]
    depth = estimate_depth(tmp_image, masks=[], detections=detections)
    assert depth["per_region"]


def test_invalid_image_depth(corrupted_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    with pytest.raises(Exception):
        estimate_depth(corrupted_image)


def test_depth_annotation_does_not_overwrite(tmp_image, monkeypatch, tmp_path):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    payload = estimate_depth(tmp_image)
    original = tmp_image.read_bytes()
    out = tmp_path / "depth_overlay.jpg"
    annotate_depth_map(tmp_image, payload["depth_map"], out)
    assert tmp_image.read_bytes() == original
    assert out.is_file()


def test_depth_failure_preserves_detection_and_segmentation(tmp_image, monkeypatch):
    from app.workers.inspection_worker import _run_optional_depth, _run_optional_segmentation

    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "false")
    monkeypatch.setenv("DEPTH_MODEL_PATH", "")
    reset_segmentation_cache()
    reset_depth_cache()
    detections = [
        {
            "class_id": 3,
            "class_name": "D40_pothole",
            "confidence": 0.9,
            "bbox_xyxy": [50, 50, 200, 200],
            "severity": "SEVERE",
        }
    ]
    seg_section, kept_dets, raw = _run_optional_segmentation(tmp_image, detections)
    assert seg_section["available"] is True
    depth_section, after = _run_optional_depth(tmp_image, kept_dets, raw)
    assert after  # detections preserved
    assert depth_section.get("error")
    assert seg_section["available"] is True


def test_no_metric_claims_without_calibration(tmp_image, monkeypatch):
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_depth_cache()
    payload = serialize_depth_for_json(estimate_depth(tmp_image))
    blob = str(payload).lower()
    assert payload["metric_depth_available"] is False
    assert "mm" not in blob and "cm" not in blob and "meter" not in blob
