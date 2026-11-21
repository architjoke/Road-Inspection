"""best.model.json drives inference image size + per-class thresholds."""

from __future__ import annotations

import json

from app.ai.model_loader import get_model_config, load_model_card
from app.ai.yolo_inference import normalize_detections


def _write_model(tmp_path, card: dict | None):
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"stub")
    if card is not None:
        (tmp_path / "best.model.json").write_text(json.dumps(card), encoding="utf-8")
    return weights


def test_card_sets_imgsz_and_thresholds_when_auto(tmp_path, monkeypatch):
    weights = _write_model(
        tmp_path, {"imgsz": 768, "class_thresholds": {"D40_pothole": 0.18}}
    )
    monkeypatch.setenv("YOLO_MODEL_PATH", str(weights))
    monkeypatch.setenv("YOLO_IMGSZ", "auto")
    cfg = get_model_config()
    assert cfg["imgsz"] == 768
    assert cfg["class_thresholds"] == {"D40_pothole": 0.18}


def test_explicit_env_imgsz_beats_card(tmp_path, monkeypatch):
    weights = _write_model(tmp_path, {"imgsz": 768})
    monkeypatch.setenv("YOLO_MODEL_PATH", str(weights))
    monkeypatch.setenv("YOLO_IMGSZ", "512")
    assert get_model_config()["imgsz"] == 512


def test_thresholds_can_be_disabled(tmp_path, monkeypatch):
    weights = _write_model(tmp_path, {"class_thresholds": {"D40_pothole": 0.1}})
    monkeypatch.setenv("YOLO_MODEL_PATH", str(weights))
    monkeypatch.setenv("YOLO_CLASS_THRESHOLDS", "off")
    assert get_model_config()["class_thresholds"] == {}


def test_no_card_keeps_previous_behaviour(tmp_path, monkeypatch):
    weights = _write_model(tmp_path, None)
    monkeypatch.setenv("YOLO_MODEL_PATH", str(weights))
    monkeypatch.setenv("YOLO_IMGSZ", "auto")
    cfg = get_model_config()
    assert load_model_card(weights) is None
    assert cfg["class_thresholds"] == {}
    assert cfg["imgsz"] == 640


def test_per_class_thresholds_filter_detections():
    raw = [
        {"class_id": 3, "class_name": "D40_pothole", "confidence": 0.2, "bbox_xyxy": [0, 0, 10, 10]},
        {"class_id": 0, "class_name": "D00_longitudinal_crack", "confidence": 0.2,
         "bbox_xyxy": [0, 0, 10, 10]},
    ]
    out = normalize_detections(
        raw, width=100, height=100, model_version="t", confidence_threshold=0.25,
        class_thresholds={"D40_pothole": 0.15},
    )
    # Pothole passes its tuned 0.15; crack falls back to the global 0.25.
    assert [d["class_name"] for d in out] == ["D40_pothole"]
