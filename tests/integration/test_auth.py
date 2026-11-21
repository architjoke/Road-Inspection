"""Offline auth login/logout checks."""

from __future__ import annotations

import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": False,
            "TEMP_UPLOAD_ROOT": tmp_path / "uploads",
            "LOG_DIR": tmp_path / "logs",
            "AUDIT_LOG_DIR": tmp_path / "logs",
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"login" in response.data.lower() or b"sign" in response.data.lower()


def test_offline_login_success_redirects(client):
    response = client.post(
        "/login",
        data={"identifier": "admin", "password": "Admin1213"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert "/dashboard" in (response.headers.get("Location") or "")


def test_offline_login_rejects_bad_password(client):
    response = client.post(
        "/login",
        data={"identifier": "admin", "password": "wrong-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid" in response.data or b"invalid" in response.data.lower()


def test_logout_requires_post(client):
    client.post("/login", data={"identifier": "admin", "password": "Admin1213"})
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code in {302, 303}
    assert "/login" in (response.headers.get("Location") or "")
