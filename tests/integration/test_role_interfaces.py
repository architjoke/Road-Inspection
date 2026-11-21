"""Role UX: viewer dashboard, contractor isolation, before-image API."""

from app import create_app
from app.services.offline_store import offline_store
from app.services.repair_service import create_repair


class _BaseUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = 7
    pk = 7
    municipality_id = 1
    username = "tester"
    email = "tester@municipality.local"
    full_name = "Test User"
    role_name = "viewer"
    dashboard_role_key = "viewer"
    display_role_name = "Viewer"

    def get_id(self):
        return "user:7"

    def has_role(self, *roles):
        return self.dashboard_role_key in roles or self.role_name in roles

    def is_super_admin(self):
        return False

    def is_contractor(self):
        return self.dashboard_role_key == "contractor"

    def is_field_worker(self):
        return self.dashboard_role_key == "field_worker"

    def is_admin(self):
        return False

    def is_viewer(self):
        return self.dashboard_role_key == "viewer"


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


def test_viewer_dashboard_does_not_500():
    user = _BaseUser()
    app = _app(user)
    client = _client(app)
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert b"System Error" not in dash.data
    assert b"Viewer Dashboard" in dash.data


def test_contractor_cannot_open_repair_manager():
    user = _BaseUser()
    user.role_name = "contractor"
    user.dashboard_role_key = "contractor"
    user.display_role_name = "Contractor"
    app = _app(user)
    client = _client(app)
    response = client.get("/repairs/verify")
    assert response.status_code == 403
    jobs = client.get("/contractor/jobs")
    assert jobs.status_code == 200
    assert b"My Repair Tasks" in jobs.data


def test_contractor_cannot_see_another_assignment():
    user = _BaseUser()
    user.role_name = "contractor"
    user.dashboard_role_key = "contractor"
    create_repair(location="Other job", assignee_id=99)
    mine = create_repair(location="My job", assignee_id=7)
    app = _app(user)
    client = _client(app)
    other = client.get("/contractor/jobs/1")
    assert other.status_code in {302, 403}
    own = client.get(f"/contractor/jobs/{mine['id']}")
    assert own.status_code == 200
    assert b"My job" in own.data


def test_before_image_api_empty_state():
    app = _app(_BaseUser())
    client = _client(app)
    response = client.get("/api/inspections/9999/before-image")
    assert response.status_code == 404
    body = response.get_json()
    assert "No before image available" in body["error"]


def test_before_image_api_from_offline_store(tmp_path):
    from datetime import datetime

    inspection = offline_store.create_inspection(
        inspection_code="INS-TEST-BEFORE",
        municipality_id=1,
        user_id=7,
        inspection_date=datetime.utcnow(),
        location_name="Test Street",
        location_latitude=26.9,
        location_longitude=80.9,
        status="pending_analysis",
    )
    offline_store.add_media(
        inspection.id,
        media_type="image",
        file_path="https://ik.imagekit.io/architmsg/demo-before.jpg",
        file_name="before.jpg",
        captured_at=datetime.utcnow(),
        is_before_image=True,
        cdn_url="https://ik.imagekit.io/architmsg/demo-before.jpg",
    )
    user = _BaseUser()
    user.dashboard_role_key = "municipal_admin"
    user.role_name = "municipal_admin"
    app = _app(user)
    client = _client(app)
    response = client.get(f"/api/inspections/{inspection.id}/before-image")
    assert response.status_code == 200
    assert "demo-before.jpg" in response.get_json()["url"]
