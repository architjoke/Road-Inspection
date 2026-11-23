"""Duplicate boxes, low-confidence cost exclusion, size plausibility, review re-pricing."""

from __future__ import annotations

from app.ai.cost_engine import cost_counts_in_total, summarize_costs
from app.ai.severity_engine import assess_severity, enrich_detections
from app.ai.yolo_inference import suppress_nested_duplicates

RATES = {
    "currency": "INR", "source": "test", "contingency_pct": 10.0,
    "mobilisation_per_site": 2000.0, "minimum_item_cost": 0.0, "rates": {}, "typical_size": {},
}


def _det(cls, conf, box, **extra):
    return {"class_id": cls, "confidence": conf, "bbox_xyxy": box, **extra}


def test_nested_same_class_box_is_dropped():
    dets = [
        _det(3, 0.9, [100, 100, 300, 300]),
        _det(3, 0.2, [120, 120, 280, 280]),   # inside the confident box
        _det(3, 0.15, [80, 80, 600, 600]),    # big low-confidence box around it
    ]
    kept = suppress_nested_duplicates(dets, min_overlap=0.6)
    assert [d["confidence"] for d in kept] == [0.9]


def test_other_classes_and_separate_boxes_survive():
    dets = [
        _det(3, 0.9, [100, 100, 300, 300]),
        _det(0, 0.5, [110, 110, 290, 290]),   # crack inside pothole: different class
        _det(3, 0.4, [400, 100, 600, 300]),   # a second, separate pothole
    ]
    assert len(suppress_nested_duplicates(dets, min_overlap=0.6)) == 3


def test_suppression_can_be_disabled():
    dets = [_det(3, 0.9, [0, 0, 10, 10]), _det(3, 0.2, [1, 1, 9, 9])]
    assert len(suppress_nested_duplicates(dets, min_overlap=0)) == 2


def test_cost_verdicts():
    assert cost_counts_in_total({"confidence": 0.7}, 0.35) is True
    assert cost_counts_in_total({"confidence": 0.2}, 0.35) is None
    assert cost_counts_in_total({"confidence": 0.2, "status": "APPROVED"}, 0.35) is True
    assert cost_counts_in_total({"confidence": 0.9, "status": "REJECTED"}, 0.35) is False


def test_low_confidence_items_are_pending_not_totalled():
    dets = [
        {"confidence": 0.8, "estimated_cost": 1000.0},
        {"confidence": 0.15, "estimated_cost": 60000.0},
        {"confidence": 0.9, "estimated_cost": 5000.0, "status": "REJECTED"},
    ]
    s = summarize_costs(dets, rates=RATES, min_confidence=0.35)
    assert s["items_total"] == 1000.0
    assert s["total"] == 1000.0 * 1.1 + 2000.0
    assert s["pending_review_count"] == 1 and s["pending_review_items_total"] == 60000.0
    assert s["rejected_count"] == 1 and s["item_count"] == 1


def test_no_counted_items_means_no_mobilisation():
    s = summarize_costs([{"confidence": 0.1, "estimated_cost": 900.0}], rates=RATES, min_confidence=0.35)
    assert s["total"] == 0.0 and s["mobilisation"] == 0.0 and s["pending_review_count"] == 1


def _pothole(box, w=678, h=452, conf=0.6):
    return {
        "class_id": 3, "class_name": "D40_pothole", "damage_type": "pothole",
        "confidence": conf, "bbox_xyxy": box, "source_width": w, "source_height": h,
    }


def test_implausible_pothole_size_falls_back_and_is_reviewed():
    # Box over the whole upper half of a close-up: the ground-plane estimate is metres wide.
    a = assess_severity(_pothole([0, 0, 565, 225]))
    m = a["measurements"]
    assert "implausible_size" in m["reasons"]
    assert m["reliable"] is False and m["size_basis"] == "image_fraction_fallback"
    assert "implausible_size_estimate" in a["evidence"]["review_reasons"]
    assert a["manual_review_required"] is True
    [enriched] = enrich_detections([_pothole([0, 0, 565, 225])])
    assert enriched["cost_estimate"]["basis"] == "typical_size_fallback"
    assert enriched["estimated_cost"] < 5000  # typical severe patch, not a 60k "7 x 9 m" item


def test_normal_pothole_keeps_its_estimate():
    m = assess_severity(_pothole([300, 330, 380, 380], w=1280, h=720))["measurements"]
    assert "implausible_size" not in m["reasons"]
    assert m["size_basis"] == "ground_plane_estimate"


def test_review_reprices_offline_inspection(monkeypatch):
    from app import create_app
    from app.services import detection_service
    from app.services.offline_store import offline_store
    from app.services.review_service import review_detection

    app = create_app({"TESTING": True, "OFFLINE_MODE": True, "WTF_CSRF_ENABLED": False})
    with app.app_context():
        monkeypatch.setattr("app.ai.cost_engine.get_cost_rates", lambda: dict(RATES))
        insp = offline_store.create_inspection(inspection_code="INS-TEST-1", municipality_id=1, user_id=1)
        saved = detection_service.save_detections_memory(insp.id, None, [
            {"class_name": "D40_pothole", "damage_type": "pothole", "confidence": 0.8,
             "estimated_cost": 1000.0, "bbox_xyxy": [0, 0, 10, 10]},
            {"class_name": "D40_pothole", "damage_type": "pothole", "confidence": 0.2,
             "estimated_cost": 3000.0, "bbox_xyxy": [20, 20, 40, 40]},
        ])
        low = next(d for d in saved if d["confidence"] == 0.2)
        review_detection(low["detection_id"], decision="approved")
        assert offline_store.get_inspection(insp.id).estimated_total_cost == round(4000.0 * 1.1 + 2000.0, 2)
        review_detection(low["detection_id"], decision="rejected")
        assert offline_store.get_inspection(insp.id).estimated_total_cost == round(1000.0 * 1.1 + 2000.0, 2)
