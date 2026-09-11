"""Phase 10 severity engine offline tests (no RDS/internet/GPU)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.severity_engine import (
    assess_severity,
    enrich_detections,
    infer_priority,
    summarize_severities,
)
from app.ai.severity_rules import classify_damage_family, get_severity_rules
from app.workers.inspection_worker import _analyze_still_image


def _det(**overrides):
    base = {
        "class_id": 3,
        "class_name": "D40_pothole",
        "damage_type": "pothole",
        "confidence": 0.9,
        "bbox_xyxy": [100, 100, 300, 280],
        "source_width": 1000,
        "source_height": 1000,
    }
    base.update(overrides)
    return base


def test_minor_moderate_severe_and_priority():
    minor = assess_severity(
        _det(
            class_name="D00_longitudinal_crack",
            damage_type="longitudinal_crack",
            confidence=0.3,
            bbox_xyxy=[10, 10, 40, 20],
        )
    )
    assert minor["level"] == "MINOR"
    assert minor["priority"] == 3

    moderate = assess_severity(
        _det(
            class_name="D10_transverse_crack",
            damage_type="transverse_crack",
            confidence=0.65,
            bbox_xyxy=[50, 50, 200, 80],
        )
    )
    assert moderate["level"] in {"MODERATE", "SEVERE"}
    assert moderate["priority"] in {1, 2}

    severe = assess_severity(
        _det(
            confidence=0.95,
            damaged_area_percentage=12.0,
            mask_pixel_count=120000,
            mean_relative_depth=0.9,
        )
    )
    assert severe["level"] == "SEVERE"
    assert severe["priority"] == 1
    assert infer_priority("SEVERE") == 1
    assert infer_priority("MODERATE") == 2
    assert infer_priority("MINOR") == 3


def test_class_specific_families():
    assert classify_damage_family("D40_pothole") == "pothole"
    assert classify_damage_family("D20_alligator_crack") == "alligator"
    assert classify_damage_family("D00_longitudinal_crack") == "longitudinal"
    assert classify_damage_family("D10_transverse_crack") == "transverse"


def test_confidence_threshold_manual_review():
    low = assess_severity(_det(confidence=0.2, damaged_area_percentage=0.5))
    assert low["manual_review_required"] is True
    assert "low_confidence" in low["evidence"]["review_reasons"]


def test_bbox_fallback_when_segmentation_disabled():
    result = assess_severity(
        _det(
            confidence=0.9,
            bbox_xyxy=[0, 0, 400, 400],  # 16% of 1000x1000
            source_width=1000,
            source_height=1000,
        )
    )
    assert result["evidence"]["segmentation"] is False
    assert result["evidence"]["fallback_used"] is True
    assert "bbox_area_fallback" in result["evidence"]["signals_used"]
    assert result["level"] in {"MODERATE", "SEVERE"}


def test_segmentation_area_evidence():
    result = assess_severity(
        _det(confidence=0.5, damaged_area_percentage=6.0, mask_pixel_count=60000)
    )
    assert result["evidence"]["segmentation"] is True
    assert result["evidence"]["fallback_used"] is False
    assert result["level"] == "SEVERE"


def test_relative_depth_evidence_not_metric():
    result = assess_severity(
        _det(confidence=0.5, mean_relative_depth=0.8, damaged_area_percentage=1.0)
    )
    assert result["evidence"]["relative_depth"] is True
    assert result["evidence"]["metric_depth"] is False
    assert result["evidence"]["calibration_available"] is False
    assert "mm" not in result["explanation"].lower()
    assert "metric" in result["explanation"].lower() or "not metric" in result["explanation"]


def test_depth_disabled_still_scores():
    result = assess_severity(_det(confidence=0.9, damaged_area_percentage=2.0))
    assert result["evidence"]["relative_depth"] is False
    assert result["level"] in {"MINOR", "MODERATE", "SEVERE"}
    assert result["recommended_action"]
    assert result["repair_type"]


def test_conflicting_evidence_flags_review():
    result = assess_severity(
        _det(
            confidence=0.3,
            damaged_area_percentage=10.0,
            mask_pixel_count=100000,
            mean_relative_depth=0.9,
        )
    )
    assert result["manual_review_required"] is True
    reasons = result["evidence"]["review_reasons"]
    assert any("conflict" in r or "ambiguous" in r or "low_confidence" in r for r in reasons)


def test_invalid_incomplete_and_empty():
    incomplete = assess_severity({"confidence": "bad", "class_name": "unknown"})
    assert incomplete["level"] in {"MINOR", "MODERATE", "SEVERE"}
    assert incomplete["evidence"]["detection"] is True

    assert enrich_detections([]) == []
    summary = summarize_severities([])
    assert summary["detection_count"] == 0
    assert summary["level"] is None


def test_multiple_detections_summary():
    dets = enrich_detections(
        [
            _det(confidence=0.3, class_name="D00_longitudinal_crack", damage_type="crack"),
            _det(confidence=0.95, damaged_area_percentage=9.0, mask_pixel_count=90000),
        ]
    )
    assert len(dets) == 2
    summary = summarize_severities(dets)
    assert summary["level"] == "SEVERE"
    assert summary["counts"]["SEVERE"] >= 1
    assert summary["evidence"]["metric_depth"] is False


def test_env_rule_overrides(monkeypatch):
    monkeypatch.setenv("SEVERITY_CONFIDENCE_MIN_REVIEW", "0.55")
    monkeypatch.setenv("SEVERITY_RULE_VERSION", "test-rules")
    rules = get_severity_rules()
    assert rules["confidence_min_review"] == 0.55
    assert rules["rule_version"] == "test-rules"


def test_worker_integration_severity_section(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_ENABLED", "false")
    monkeypatch.setenv("DEPTH_ENABLED", "false")
    path = tmp_path / "road.jpg"
    Image.new("RGB", (640, 480), color=(90, 90, 90)).save(path, format="JPEG")
    result = _analyze_still_image(path)
    assert "severity" in result
    assert isinstance(result["detections"], list)
    for det in result["detections"]:
        assert det.get("severity") in {"MINOR", "MODERATE", "SEVERE"}
        assert "severity_assessment" in det
        assert det["severity_assessment"]["evidence"]["metric_depth"] is False


def test_worker_with_seg_and_depth_mock(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_ENABLED", "true")
    monkeypatch.setenv("SEGMENTATION_FORCE_MOCK", "true")
    monkeypatch.setenv("DEPTH_ENABLED", "true")
    monkeypatch.setenv("DEPTH_FORCE_MOCK", "true")
    path = tmp_path / "road2.jpg"
    Image.new("RGB", (640, 480), color=(70, 70, 70)).save(path, format="JPEG")
    result = _analyze_still_image(path)
    assert result["severity"] is not None
    assert Path(result["annotated_path"]).is_file()


def test_no_metric_claims_in_assessment_blob():
    result = assess_severity(_det(mean_relative_depth=0.7, damaged_area_percentage=4.0))
    blob = str(result).lower()
    assert result["evidence"]["metric_depth"] is False
    assert "cubic" not in blob
    assert "millimeter" not in blob
