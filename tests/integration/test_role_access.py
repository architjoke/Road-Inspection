"""Offline role gate checks."""

from __future__ import annotations

import pytest

from app import create_app


class _RoleUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = 2
    pk = 2
    municipality_id = 1

    def __init__(self, role_name: str):
        self.role_name = role_name
        self.dashboard_role_key = role_name

    def get_id(self):
        return f"user:{self.id}"

    def has_role(self, *roles):
        allowed = {str(r).lower() for r in roles}
        return self.role_name.lower() in allowed

    def is_super_admin(self):
        return self.role_name == "super_admin"


@pytest.fixture
def make_app(tmp_path):
    def _factory(role_name: str):
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
        fake = _RoleUser(role_name)

        @application.login_manager.user_loader
        def _load(_uid):
            return fake

        return application, fake

    return _factory


def test_viewer_blocked_from_protected_admin_pages(make_app):
    app, fake = make_app("viewer")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = fake.get_id()
        sess["_fresh"] = True
    response = client.get("/users", follow_redirects=False)
    assert response.status_code in {302, 403, 404}


def test_admin_can_open_dashboard(make_app):
    app, fake = make_app("municipal_admin")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = fake.get_id()
        sess["_fresh"] = True
    response = client.get("/dashboard")
    assert response.status_code == 200
