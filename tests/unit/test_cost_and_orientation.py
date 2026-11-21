"""Quantity-based cost engine, EXIF orientation fix and DB detection mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.ai.cost_engine import (
    DEFAULT_COST_RATES,
    estimate_detection_cost,
    get_cost_rates,
    summarize_costs,
)
from app.ai.severity_engine import enrich_detections
from app.utils.image_orientation import ensure_upright, upright_size


def _pothole(box, **extra):
    return {
        "class_id": 3,
        "class_name": "D40_pothole",
        "damage_type": "pothole",
        "confidence": 0.9,
        "bbox_xyxy": box,
        "source_width": 1000,
        "source_height": 1000,
        **extra,
    }


# --- cost engine -----------------------------------------------------------


def test_bigger_pothole_costs_more_than_small_one():
    small, big = enrich_detections(
        [_pothole([450, 750, 550, 820]), _pothole([350, 650, 650, 900])]
    )
    assert small["cost_estimate"]["basis"] == "estimated_size"
    assert small["cost_estimate"]["unit"] == "m3"
    assert big["estimated_cost"] > small["estimated_cost"]
    # Volume follows the patch footprint x assumed depth.
    assert big["estimated_volume_m3"] > small["estimated_volume_m3"]


def test_two_same_severity_potholes_of_different_size_cost_differently():
    rates = get_cost_rates()
    a = estimate_detection_cost(
        "pothole",
        "SEVERE",
        {"reliable": True, "width_m": 0.6, "length_m": 0.6, "estimated_depth_mm": 75},
        rates=rates,
    )
    b = estimate_detection_cost(
        "pothole",
        "SEVERE",
        {"reliable": True, "width_m": 1.5, "length_m": 1.2, "estimated_depth_mm": 75},
        rates=rates,
    )
    assert b["amount"] > a["amount"] * 2


def test_crack_cost_scales_with_length():
    rates = get_cost_rates()
    short = estimate_detection_cost(
        "longitudinal", "MODERATE", {"reliable": True, "width_m": 0.1, "length_m": 4.0,
                                     "crack_length_m": 4.0}, rates=rates
    )
    long_ = estimate_detection_cost(
        "longitudinal", "MODERATE", {"reliable": True, "width_m": 0.1, "length_m": 12.0,
                                     "crack_length_m": 12.0}, rates=rates
    )
    assert short["unit"] == "m"
    assert long_["amount"] == pytest.approx(short["amount"] * 3)


def test_unreliable_size_uses_typical_quantity_and_minimum():
    cost = estimate_detection_cost("crack", "MINOR", {"reliable": False})
    assert cost["basis"] == "typical_size_fallback"
    assert cost["amount"] >= DEFAULT_COST_RATES["minimum_item_cost"]


def test_inspection_total_adds_contingency_and_one_mobilisation():
    dets = [
        {"cost_estimate": {"amount": 10000.0}},
        {"cost_estimate": {"amount": 5000.0}},
    ]
    summary = summarize_costs(dets)
    assert summary["items_total"] == 15000.0
    assert summary["contingency"] == pytest.approx(1500.0)
    assert summary["mobilisation"] == DEFAULT_COST_RATES["mobilisation_per_site"]
    assert summary["total"] == pytest.approx(15000 + 1500 + summary["mobilisation"])
    assert summarize_costs([])["total"] == 0.0


def test_db_rates_override_defaults(monkeypatch):
    import app.ai.severity_rules as rules_mod

    def fake_setting(key):
        if key == "repair_cost_rates":
            return {"rates": {"pothole_patch_per_m3": 20000.0}, "source": "Test SOR 2026"}
        return None

    monkeypatch.setattr(rules_mod, "load_db_setting", fake_setting)
    rates = get_cost_rates()
    assert rates["rates"]["pothole_patch_per_m3"] == 20000.0
    # Untouched keys keep defaults (deep merge).
    assert rates["rates"]["crack_seal_per_m"] == DEFAULT_COST_RATES["rates"]["crack_seal_per_m"]
    assert rates["source"] == "Test SOR 2026"


# --- EXIF orientation ------------------------------------------------------


def _rotated_jpeg(path: Path, orientation: int = 6) -> Path:
    img = Image.new("RGB", (400, 200), (120, 120, 120))
    exif = Image.Exif()
    exif[0x0112] = orientation
    img.save(path, exif=exif.tobytes())
    return path


def test_upright_size_matches_opencv(tmp_path):
    import cv2
    import numpy as np

    path = _rotated_jpeg(tmp_path / "portrait.jpg")
    h, w = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR).shape[:2]
    assert upright_size(path) == (w, h) == (200, 400)


def test_ensure_upright_writes_rotated_copy_and_keeps_original(tmp_path):
    path = _rotated_jpeg(tmp_path / "portrait.jpg")
    upright = ensure_upright(path)
    assert upright != path and upright.is_file()
    with Image.open(upright) as im:
        assert im.size == (200, 400)
        assert im.getexif().get(0x0112, 1) == 1
    with Image.open(path) as im:
        assert im.size == (400, 200)  # original untouched


def test_ensure_upright_noop_for_normal_image(tmp_image):
    assert ensure_upright(tmp_image) == tmp_image


def test_worker_annotates_rotated_photo_in_upright_frame(tmp_path, monkeypatch):
    from app.workers.inspection_worker import _analyze_still_image

    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    monkeypatch.setenv("SEGMENTATION_ENABLED", "false")
    monkeypatch.setenv("DEPTH_ENABLED", "false")
    path = _rotated_jpeg(tmp_path / "phone.jpg")
    result = _analyze_still_image(path)
    det = result["detections"][0]
    assert (det["source_width"], det["source_height"]) == (200, 400)
    with Image.open(result["annotated_path"]) as im:
        assert im.size == (200, 400)
    assert "estimated_cost" in det and det["estimated_cost"] > 0
    assert result["severity"]["cost"]["total"] > 0


# --- DB mapping for the live Supabase schema --------------------------------


LIVE_COLUMNS = {
    "inspection_id", "media_id", "detection_code", "damage_type", "damage_class",
    "severity_level", "priority_level", "confidence_score", "bounding_box_x1",
    "bounding_box_y1", "bounding_box_x2", "bounding_box_y2", "area_sq_pixels",
    "area_sq_meters", "estimated_depth_mm", "estimated_volume_m3",
    "estimated_repair_cost", "recommended_repair_type", "gps_latitude",
    "gps_longitude", "status", "is_verified", "is_false_positive", "detected_at",
    "created_at", "updated_at",
}


def test_db_insert_values_match_live_constraints():
    from app.services.detection_service import _db_insert_values

    det = enrich_detections([_pothole([350, 650, 650, 900])])[0]
    values = _db_insert_values(
        det, LIVE_COLUMNS, inspection_id=7, media_id=3, latitude=26.9, longitude=75.8
    )
    assert values["severity_level"] in {"minor", "moderate", "severe"}  # CHECK constraint
    assert 1 <= values["priority_level"] <= 3
    assert values["gps_latitude"] == 26.9 and values["gps_longitude"] == 75.8
    assert (values["bounding_box_x1"], values["bounding_box_y2"]) == (350, 900)
    assert values["estimated_repair_cost"] == det["estimated_cost"]
    assert values["area_sq_meters"] == det["area_sq_meters"]
    assert values["detection_code"].startswith("DET-7-")
    assert "bounding_box" not in values


def test_db_row_round_trip_normalisation():
    from app.services.detection_service import _normalize_db_detection_row

    row = {
        "detection_id": 11, "inspection_id": 7, "damage_type": "pothole",
        "severity_level": "severe", "priority_level": 1, "confidence_score": 0.81,
        "bounding_box_x1": 1, "bounding_box_y1": 2, "bounding_box_x2": 30,
        "bounding_box_y2": 40, "estimated_repair_cost": 5400, "area_sq_meters": 0.4,
        "recommended_repair_type": "surface patch",
    }
    out = _normalize_db_detection_row(row, set(row))
    assert out["severity"] == "SEVERE"
    assert out["bounding_box"] == {"x1": 1, "y1": 2, "x2": 30, "y2": 40}
    assert out["estimated_cost"] == 5400.0
    assert out["repair_type"] == "surface patch"
