"""Field Worker live camera capture API (offline, no RDS)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app import create_app
from app.utils.location_format import format_capture_location


class _FieldWorker:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = 7
    pk = 7
    municipality_id = 1
    username = "worker"
    email = "worker@municipality.local"
    full_name = "Field Worker"
    role_name = "field_worker"
    dashboard_role_key = "field_worker"
    display_role_name = "Field Worker"

    def get_id(self):
        return "user:7"

    def has_role(self, *roles):
        return self.dashboard_role_key in roles or self.role_name in roles

    def is_super_admin(self):
        return False

    def is_contractor(self):
        return False

    def is_field_worker(self):
        return True

    def is_admin(self):
        return False

    def is_viewer(self):
        return False


class _Viewer(_FieldWorker):
    username = "viewer"
    role_name = "viewer"
    dashboard_role_key = "viewer"
    display_role_name = "Viewer"

    def is_field_worker(self):
        return False


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (1280, 720), color=(80, 80, 80)).save(buf, format="JPEG")
    return buf.getvalue()


def _app(user):
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
        }
    )

    @application.login_manager.user_loader
    def _load(_uid):
        return user

    return application


def _client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:7"
        sess["_fresh"] = True
    return client


def test_format_capture_location():
    assert (
        format_capture_location(
            {
                "street_name": "MG Road",
                "landmark": "City Hospital",
                "ward": "Civil Lines",
                "city": "Etawah",
            }
        )
        == "MG Road, near City Hospital, Civil Lines Ward, Etawah"
    )
    assert format_capture_location({}) == "Unknown Location"


def test_dashboard_alias_redirects():
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.get("/field_worker/dashboard")
    assert response.status_code in {200, 302}
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert b"Capture New Inspection" in dash.data
    assert b"fw-video" in dash.data


def test_capture_success(monkeypatch):
    monkeypatch.setattr(
        "app.routes.field_worker.reverse_geocode",
        lambda lat, lng: {
            "street_name": "MG Road",
            "landmark": "City Hospital",
            "ward": "Civil Lines",
            "city": "Etawah",
        },
    )
    monkeypatch.setattr(
        "app.routes.field_worker.assign_ward_from_gps",
        lambda lat, lng, municipality_id=None: {"ward_id": 4, "ward_name": "Civil Lines"},
    )
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={
            "image": (BytesIO(_jpeg_bytes()), "capture.jpg"),
            "latitude": "26.7765",
            "longitude": "79.0215",
            "accuracy": "12.4",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["success"] is True
    assert body["inspection_id"]
    assert "MG Road" in body["location_name"]
    assert body["coordinates"]["latitude"] == pytest.approx(26.7765)


def test_capture_requires_image():
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={"latitude": "26.7", "longitude": "79.0"},
    )
    assert response.status_code == 400


def test_capture_requires_gps():
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={"image": (BytesIO(_jpeg_bytes()), "capture.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "GPS" in response.get_json()["error"]


def test_capture_rejects_invalid_gps():
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={
            "image": (BytesIO(_jpeg_bytes()), "capture.jpg"),
            "latitude": "200",
            "longitude": "79.0",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_capture_forbidden_for_viewer():
    app = _app(_Viewer())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={
            "image": (BytesIO(_jpeg_bytes()), "capture.jpg"),
            "latitude": "26.7",
            "longitude": "79.0",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 403


def test_capture_unknown_location_when_geocode_fails(monkeypatch):
    monkeypatch.setattr("app.routes.field_worker.reverse_geocode", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.routes.field_worker.assign_ward_from_gps",
        lambda *a, **k: None,
    )
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={
            "image": (BytesIO(_jpeg_bytes()), "capture.jpg"),
            "latitude": "26.7765",
            "longitude": "79.0215",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    assert response.get_json()["location_name"] == "Unknown Location"
    assert response.get_json()["ward_id"] in (None, 0)


def test_assign_ward_offline_returns_none():
    from app.services.ward_service import assign_ward_from_gps

    app = _app(_FieldWorker())
    with app.app_context():
        assert assign_ward_from_gps(26.77, 79.02, municipality_id=1) is None


def test_capture_db_failure_returns_500(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.routes.field_worker.create_inspection_with_media", _boom)
    monkeypatch.setattr("app.routes.field_worker.reverse_geocode", lambda *a, **k: None)
    monkeypatch.setattr("app.routes.field_worker.assign_ward_from_gps", lambda *a, **k: None)
    app = _app(_FieldWorker())
    client = _client(app)
    response = client.post(
        "/field_worker/api/capture",
        data={
            "image": (BytesIO(_jpeg_bytes()), "capture.jpg"),
            "latitude": "26.7765",
            "longitude": "79.0215",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 500
