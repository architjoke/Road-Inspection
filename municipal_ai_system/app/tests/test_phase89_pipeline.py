"""Phase 8/9 pipeline integration (offline; optional features)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.depth_estimation import reset_depth_cache
from app.ai.model_loader import reset_model_cache
from app.ai.segmentation import reset_segmentation_cache
from app.workers.inspection_worker import _analyze_still_image


@pytest.fixture
def still_image(tmp_path):
    from PIL import Image

    path = tmp_path / "road.jpg"
    Image.new("RGB", (640, 480), color=(80, 80, 80)).save(path, format="JPEG")
    return path


def _enable_mock_seg_depth(monkeypatch, *, seg=True, depth=True):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true" if seg else "false")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    monkeypatch.setenv("DEPTH_ENABLED", "true" if depth else "false")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    reset_model_cache()
    reset_segmentation_cache()
    reset_depth_cache()


def test_pipeline_both_disabled(still_image, monkeypatch):
    _enable_mock_seg_depth(monkeypatch, seg=False, depth=False)
    result = _analyze_still_image(still_image)
    assert result["segmentation"]["enabled"] is False
    assert result["depth"]["enabled"] is False
    assert isinstance(result["detections"], list)
    assert Path(result["annotated_path"]).is_file()


def test_pipeline_seg_only(still_image, monkeypatch):
    _enable_mock_seg_depth(monkeypatch, seg=True, depth=False)
    result = _analyze_still_image(still_image)
    assert result["segmentation"]["available"] is True
    assert result["depth"]["enabled"] is False
    assert result["segmentation"].get("overlay_path")


def test_pipeline_depth_only(still_image, monkeypatch):
    _enable_mock_seg_depth(monkeypatch, seg=False, depth=True)
    result = _analyze_still_image(still_image)
    assert result["depth"]["available"] is True
    assert result["depth"]["metric_depth_available"] is False
    assert result["segmentation"]["enabled"] is False


def test_pipeline_both_enabled(still_image, monkeypatch):
    _enable_mock_seg_depth(monkeypatch, seg=True, depth=True)
    result = _analyze_still_image(still_image)
    assert result["segmentation"]["available"] is True
    assert result["depth"]["available"] is True
    assert Path(result["annotated_path"]).is_file()


def test_pipeline_missing_seg_model_preserves_detections(still_image, monkeypatch):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "false")
    monkeypatch.setenv("SEGMENTATION_MODEL_PATH", "")
    monkeypatch.setenv("DEPTH_ENABLED", "false")
    reset_model_cache()
    reset_segmentation_cache()
    result = _analyze_still_image(still_image)
    assert isinstance(result["detections"], list)
    assert result["segmentation"].get("error")
    assert result["errors"]
