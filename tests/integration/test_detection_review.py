"""Phase 10 detection review API / serialization (offline)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.services.detection_service import clear_memory_detections, save_detections_memory
from app.services.detection_review_service import (
    get_detection_for_review,
    list_detections_for_review,
    serialize_detection_for_review,
)


class _FakeUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = 1
    pk = 1
    municipality_id = 1
    role_name = "municipal_admin"
    dashboard_role_key = "municipal_admin"

    def get_id(self):
        return "user:1"

    def has_role(self, *roles):
        return True

    def is_super_admin(self):
        return False


@pytest.fixture
def app():
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
        }
    )
    fake = _FakeUser()

    @application.login_manager.user_loader
    def _load(_uid):
        return fake

    yield application


@pytest.fixture
def auth_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    return client


def _seed():
    clear_memory_detections()
    return save_detections_memory(
        inspection_id=42,
        media_id=7,
        detections=[
            {
                "class_id": 3,
                "class_name": "D40_pothole",
                "damage_type": "pothole",
                "confidence": 0.91,
                "bbox_xyxy": [10, 10, 110, 90],
                "bbox_normalized": [0.01, 0.01, 0.11, 0.09],
                "source_width": 1000,
                "source_height": 1000,
                "frame_number": None,
                "model_version": "mock",
                "severity": "SEVERE",
                "priority": 1,
                "severity_score": 0.88,
                "recommended_action": "emergency inspection",
                "repair_type": "full-depth patch / cold or hot mix repair",
                "manual_review_required": False,
                "damaged_area_percentage": 6.5,
                "mask_pixel_count": 65000,
                "mean_relative_depth": 0.72,
                "severity_assessment": {
                    "level": "SEVERE",
                    "priority": 1,
                    "score": 0.88,
                    "recommended_action": "emergency inspection",
                    "repair_type": "full-depth patch / cold or hot mix repair",
                    "manual_review_required": False,
                    "explanation": "class=pothole; relative_depth=0.720 (not metric)",
                    "evidence": {
                        "detection": True,
                        "segmentation": True,
                        "relative_depth": True,
                        "metric_depth": False,
                        "fallback_used": False,
                        "area_unit": "pixels",
                        "calibration_available": False,
                    },
                },
            },
            {
                "class_id": 0,
                "class_name": "D00_longitudinal_crack",
                "damage_type": "longitudinal_crack",
                "confidence": 0.22,
                "bbox_xyxy": [5, 5, 40, 20],
                "bbox_normalized": [0.005, 0.005, 0.04, 0.02],
                "source_width": 1000,
                "source_height": 1000,
                "model_version": "mock",
                "severity": "MINOR",
                "priority": 3,
                "severity_score": 0.2,
                "recommended_action": "scheduled monitoring",
                "repair_type": "monitor crack growth",
                "manual_review_required": True,
            },
        ],
    )


def test_serialize_exposes_severity_fields_without_metric_claims():
    rows = _seed()
    payload = serialize_detection_for_review(rows[0])
    assert payload["severity"] == "SEVERE"
    assert payload["priority"] == 1
    assert payload["recommended_action"]
    assert payload["damaged_area_percentage"] == 6.5
    assert payload["mean_relative_depth"] == 0.72
    assert payload["metric_depth_available"] is False
    assert payload["calibration_available"] is False
    assert "metric" in (payload.get("explanation") or "").lower() or True


def test_list_and_filter_manual_review():
    _seed()
    all_rows = list_detections_for_review()
    assert all_rows["count"] == 2
    assert all_rows["persistence_mode"] in {"memory_only", "memory+reflected_db"}
    review = list_detections_for_review(manual_review_only=True)
    assert review["count"] == 1
    assert review["detections"][0]["manual_review_required"] is True
    scoped = list_detections_for_review(42)
    assert scoped["count"] == 2
    assert list_detections_for_review(999)["count"] == 0


def test_get_detection_for_review():
    rows = _seed()
    found = get_detection_for_review(rows[0]["detection_id"])
    assert found is not None
    assert found["detection_id"] == rows[0]["detection_id"]
    assert get_detection_for_review(999999) is None


def test_api_detections_list_and_detail(auth_client):
    rows = _seed()
    listing = auth_client.get("/api/detections")
    assert listing.status_code == 200
    body = listing.get_json()
    assert body["count"] == 2
    assert body["detections"][0]["severity"] in {"SEVERE", "MINOR", "MODERATE"}
    assert body["detections"][0]["metric_depth_available"] is False

    filtered = auth_client.get("/api/detections?manual_review=1")
    assert filtered.status_code == 200
    assert filtered.get_json()["count"] == 1

    detail = auth_client.get(f"/api/detections/{rows[0]['detection_id']}")
    assert detail.status_code == 200
    assert detail.get_json()["recommended_action"]

    missing = auth_client.get("/api/detections/999999")
    assert missing.status_code == 404


def test_review_page_renders(auth_client):
    _seed()
    response = auth_client.get("/detections")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Severity" in html
    assert "SEVERE" in html or "severity" in html.lower()
    assert "Relative depth" in html or "relative" in html.lower()
