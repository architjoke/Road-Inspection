"""Finished-product page smoke tests (offline, no RDS)."""

from __future__ import annotations

from app import create_app
from app.services.repair_service import create_repair


class _FakeUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = 1
    pk = 1
    municipality_id = 1
    username = "admin"
    email = "admin@municipality.local"
    full_name = "Test Admin"
    role_name = "municipal_admin"
    dashboard_role_key = "municipal_admin"
    display_role_name = "Municipal Admin"

    def get_id(self):
        return "user:1"

    def has_role(self, *roles):
        return True

    def is_super_admin(self):
        return False

    def is_contractor(self):
        return False


def _app():
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

    return application


def _client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    return client


def test_admin_pages_render():
    app = _app()
    client = _client(app)
    for path in (
        "/dashboard",
        "/users",
        "/users/new",
        "/wards",
        "/wards/new",
        "/detections",
        "/repairs",
        "/repairs/verify",
        "/repairs/new",
        "/repairs/locations",
        "/map",
        "/reports",
        "/profile",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_create_user_and_repair_job():
    app = _app()
    client = _client(app)
    created = client.post(
        "/users/new",
        data={
            "username": "worker1",
            "email": "worker1@example.com",
            "full_name": "Field Worker One",
            "password": "Password12",
            "role_id": "field_worker",
            "is_active": "1",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"worker1" in created.data

    job = create_repair(location="Main Street", damage_type="pothole", severity="SEVERE")
    detail = client.get(f"/repairs/{job['id']}")
    assert detail.status_code == 200
    assert b"Main Street" in detail.data


def test_viewer_dashboard_uses_inspections_list():
    app = _app()

    class Viewer(_FakeUser):
        role_name = "viewer"
        dashboard_role_key = "viewer"
        display_role_name = "Viewer"

        def has_role(self, *roles):
            return "viewer" in roles

    @app.login_manager.user_loader
    def _load(_uid):
        return Viewer()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"protected.inspections" not in response.data
    assert b"/inspections/" in response.data or b"Inspections" in response.data
