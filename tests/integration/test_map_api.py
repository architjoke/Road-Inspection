"""Phase 11 map / Haversine / offline GeoJSON tests (no RDS)."""

from __future__ import annotations

import pytest

from app import create_app
from app.services.detection_service import clear_memory_detections, save_detections_memory
from app.services.map_service import (
    filter_geojson_by_radius,
    haversine_meters,
    map_detections_payload,
    map_radius_payload,
    offline_map_detections_geojson,
)
from app.services.offline_store import offline_store
from app.ai.severity_engine import enrich_detections


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
            "YOLO_FORCE_MOCK": True,
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


def _seed_offline_point(lat=16.705, lng=74.2431, severity="SEVERE", priority=1):
    offline_store.clear()
    clear_memory_detections()
    inspection = offline_store.create_inspection(
        inspection_code="INS-MAP-1",
        municipality_id=1,
        user_id=1,
        ward_id=None,
        location_name="Tarabaai Park",
        location_latitude=lat,
        location_longitude=lng,
        status="pending_review",
    )
    offline_store.add_media(
        inspection.id,
        media_type="image",
        file_path="/tmp/road.jpg",
        file_name="road.jpg",
        thumbnail_path="/tmp/road_thumb.jpg",
        cdn_url=None,
    )
    dets = enrich_detections(
        [
            {
                "class_id": 3,
                "class_name": "D40_pothole",
                "damage_type": "pothole",
                "confidence": 0.9,
                "bbox_xyxy": [10, 10, 100, 80],
                "source_width": 640,
                "source_height": 480,
                "model_version": "mock",
            }
        ]
    )
    dets[0]["severity"] = severity
    dets[0]["priority"] = priority
    dets[0]["recommended_action"] = "emergency inspection"
    save_detections_memory(inspection.id, 1, dets)
    return inspection


def test_haversine_known_distance():
    # ~111 km per degree latitude
    dist = haversine_meters(16.0, 74.0, 17.0, 74.0)
    assert 110000 < dist < 112500


def test_offline_geojson_structure_and_filters():
    _seed_offline_point()
    payload = offline_map_detections_geojson()
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) >= 1
    props = payload["features"][0]["properties"]
    assert props["severity"] == "SEVERE"
    assert props["priority"] == 1
    assert props["damage_type"]
    assert props["latitude"] is not None
    assert props["thumbnail_url"]
    assert props["metric_depth_available"] is False

    filtered = offline_map_detections_geojson(severity=["MINOR"])
    assert filtered["features"] == []

    by_pri = offline_map_detections_geojson(priority=["1"])
    assert len(by_pri["features"]) >= 1


def test_radius_haversine_filter():
    _seed_offline_point(16.705, 74.2431)
    base = offline_map_detections_geojson()
    near = filter_geojson_by_radius(base, 16.705, 74.2431, 500)
    assert len(near["features"]) >= 1
    far = filter_geojson_by_radius(base, 18.0, 75.0, 500)
    assert far["features"] == []


def test_invalid_gps_skipped():
    offline_store.clear()
    clear_memory_detections()
    inspection = offline_store.create_inspection(
        inspection_code="INS-BAD",
        municipality_id=1,
        user_id=1,
        location_latitude=999,
        location_longitude=74.0,
        location_name="bad",
    )
    save_detections_memory(
        inspection.id,
        None,
        enrich_detections(
            [
                {
                    "class_name": "D40_pothole",
                    "confidence": 0.5,
                    "bbox_xyxy": [1, 1, 2, 2],
                    "source_width": 10,
                    "source_height": 10,
                }
            ]
        ),
    )
    payload = offline_map_detections_geojson()
    assert payload["features"] == []


def test_empty_dataset():
    offline_store.clear()
    clear_memory_detections()
    payload = map_detections_payload(prefer_offline=True)
    assert payload["features"] == []
    assert payload["mode"] == "offline"


def test_map_api_endpoints(auth_client):
    _seed_offline_point()
    response = auth_client.get("/api/map/detections")
    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "FeatureCollection"
    assert body["features"]
    assert body["features"][0]["properties"]["severity"] in {
        "SEVERE",
        "MODERATE",
        "MINOR",
    }

    sev = auth_client.get("/api/map/detections?severity=SEVERE")
    assert sev.status_code == 200
    assert sev.get_json()["features"]

    radius = auth_client.get(
        "/api/inspections/radius?lat=16.705&lng=74.2431&radius=1000"
    )
    assert radius.status_code == 200
    rbody = radius.get_json()
    assert rbody["type"] == "FeatureCollection"
    assert rbody.get("mode") in {"haversine", "offline", "postgis", "postgis+offline"}

    bad = auth_client.get("/api/inspections/radius?lat=999&lng=74&radius=100")
    assert bad.status_code == 400

    legacy = auth_client.get("/api/inspections/geojson")
    assert legacy.status_code == 200


def test_capture_upload_with_gps_payload(auth_client, tmp_path):
    from io import BytesIO

    from PIL import Image

    offline_store.clear()
    clear_memory_detections()
    img = BytesIO()
    Image.new("RGB", (1280, 720), color=(50, 50, 50)).save(img, format="JPEG")
    img.seek(0)

    response = auth_client.post(
        "/api/inspections/upload",
        data={
            "file": (img, "camera_capture.jpg"),
            "location_name": "Camera capture test",
            "location_latitude": "16.7050",
            "location_longitude": "74.2431",
            "gps_manual_override": "1",
            "gps_accuracy_meters": "12",
            "notes": "phase11 capture",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert payload["inspection_id"]
    assert payload["gps"]["lat"] == pytest.approx(16.705, abs=1e-3)
    assert payload["analysis_task"]

    # Map should eventually include the inspection point (even before poll)
    geo = auth_client.get("/api/map/detections").get_json()
    assert any(
        f["properties"].get("inspection_id") == payload["inspection_id"]
        for f in geo["features"]
    )


def test_radius_km_alias(auth_client):
    _seed_offline_point()
    response = auth_client.get(
        "/api/inspections/radius?lat=16.705&lng=74.2431&radius_km=1"
    )
    assert response.status_code == 200
    assert response.get_json()["features"]


def test_map_radius_payload_helper():
    _seed_offline_point()
    payload = map_radius_payload(
        latitude=16.705,
        longitude=74.2431,
        radius_meters=800,
        prefer_offline=True,
    )
    assert payload["mode"] == "haversine"
    assert payload["features"]
