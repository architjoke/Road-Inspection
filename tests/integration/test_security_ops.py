"""Phase 14 security, health, logging, and validation tests (offline)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services.audit_trail import list_audit_events, record_audit_event
from app.services.validation_service import (
    validate_coordinates,
    validate_report_format,
    validate_upload,
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
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": True,
            "RATELIMIT_ENABLED": True,
            "SECURITY_HEADERS_ENABLED": True,
            "LOG_DIR": tmp_path / "logs",
            "AUDIT_LOG_DIR": tmp_path / "logs",
            "REPORTS_OUTPUT_ROOT": tmp_path / "reports",
            "TEMP_UPLOAD_ROOT": tmp_path / "uploads",
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


def test_health_payload_offline(app):
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] in {"ok", "degraded"}
    assert body["offline_mode"] is True
    assert body["rds_contacted"] is False
    assert body["phase"].startswith("phase_14")
    assert body["components"]["database"]["contacted"] is False
    assert body["components"]["memory_store"]["status"] == "ok"
    assert body["components"]["executor"]["max_workers"] >= 1
    assert "model" in body["components"]
    assert "storage" in body["components"]


def test_security_headers_present(app):
    client = app.test_client()
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response.headers.get("Referrer-Policy")


def test_api_404_json(app):
    client = app.test_client()
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.is_json
    assert "error" in response.get_json()


def test_csrf_blocks_unprotected_form_post(app):
    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": "x", "password": "y"},
        follow_redirects=False,
    )
    # Missing CSRF should fail closed (400) when CSRF is enabled.
    assert response.status_code == 400


def test_validate_coordinates_and_report_format():
    assert validate_coordinates("16.7", "74.2")["valid"] is True
    assert validate_coordinates("999", "74")["valid"] is False
    assert validate_coordinates("abc", "1")["valid"] is False
    assert validate_report_format("pdf")["format"] == "pdf"
    assert validate_report_format("xlsx")["format"] == "excel"
    assert validate_report_format("exe")["valid"] is False


def test_validate_upload_rejects_traversal(app):
    with app.app_context():
        storage = FileStorage(
            stream=BytesIO(b"not-real"),
            filename="../evil.jpg",
            content_type="image/jpeg",
        )
        result = validate_upload(storage)
        assert result["valid"] is False


def test_audit_trail_records(app, tmp_path):
    with app.app_context():
        app.config["AUDIT_LOG_DIR"] = tmp_path / "logs"
        (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
        event = record_audit_event(
            "test.action",
            actor="tester",
            resource_type="unit",
            resource_id=1,
            details={"k": "v"},
        )
        assert event["action"] == "test.action"
        events = list_audit_events()
        assert any(e["action"] == "test.action" for e in events)
        trail = tmp_path / "logs" / "audit_trail.jsonl"
        assert trail.is_file()
        assert "test.action" in trail.read_text(encoding="utf-8")


def test_detections_rate_limit_endpoint_ok(auth_client):
    response = auth_client.get("/api/detections")
    assert response.status_code == 200
    assert "count" in response.get_json()


def test_report_export_rejects_bad_format(auth_client, app):
    from app.services.offline_store import offline_store

    with app.app_context():
        insp = offline_store.create_inspection(
            inspection_code="INS-SEC-1",
            municipality_id=1,
            user_id=1,
            location_name="Sec",
            location_latitude=16.7,
            location_longitude=74.2,
        )
        insp_id = insp.id

    response = auth_client.post(
        "/api/reports/export",
        json={"inspection_id": insp_id, "format": "exe"},
    )
    assert response.status_code == 400
    assert "format" in response.get_json()["error"].lower()


def test_logging_dir_created(app):
    assert Path(app.config["LOG_DIR"]).exists()
