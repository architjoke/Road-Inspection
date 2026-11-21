"""Offline inspection page access checks."""

from __future__ import annotations

import pytest

from app import create_app


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

    def is_field_worker(self):
        return False

    def is_municipal_admin(self):
        return True


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


def test_inspections_list_requires_login(app):
    client = app.test_client()
    response = client.get("/inspections/", follow_redirects=False)
    assert response.status_code in {302, 401}


def test_inspections_list_ok_when_authed(auth_client):
    response = auth_client.get("/inspections/")
    assert response.status_code == 200


def test_inspections_new_form_ok(auth_client):
    response = auth_client.get("/inspections/new")
    assert response.status_code == 200
