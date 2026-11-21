"""API + task lifecycle integration (offline, no RDS)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app import create_app
from app.workers.task_store import (
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    task_store,
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

    def is_field_worker(self):
        return False


@pytest.fixture
def app():
    application = create_app(
        {
            "TESTING": True,
            "YOLO_FORCE_MOCK": True,
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
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    return client


def test_analyze_invalid_inspection_returns_404(auth_client, monkeypatch, app):
    from unittest.mock import MagicMock

    mock_session = MagicMock()
    mock_session.get.return_value = None
    monkeypatch.setattr("app.routes.api.db.session", mock_session)

    response = auth_client.post("/api/inspections/999999/analyze")
    assert response.status_code == 404
    assert "not found" in response.get_json()["error"].lower()


def test_task_status_and_result_lifecycle(auth_client):
    task = task_store.create(inspection_id=10, media_id=3)

    pending = auth_client.get(f"/api/tasks/{task.task_id}")
    assert pending.status_code == 200
    assert pending.get_json()["status"] == TASK_PENDING

    in_progress = auth_client.get(f"/api/tasks/{task.task_id}/result")
    assert in_progress.status_code == 202

    task_store.update(
        task.task_id,
        status=TASK_SUCCEEDED,
        completed_at=datetime.utcnow(),
        progress=100,
        result_summary={"detection_count": 1, "detections": []},
    )
    done = auth_client.get(f"/api/tasks/{task.task_id}/result")
    assert done.status_code == 200
    body = done.get_json()
    assert body["status"] == TASK_SUCCEEDED
    assert body["result_summary"]["detection_count"] == 1

    again = auth_client.get(f"/api/tasks/{task.task_id}/result")
    assert again.status_code == 200
    assert again.get_json()["task_id"] == task.task_id


def test_failed_task_result_includes_error(auth_client):
    task = task_store.create(inspection_id=11)
    task_store.update(
        task.task_id,
        status=TASK_FAILED,
        completed_at=datetime.utcnow(),
        error_message="Local media missing",
        started_at=datetime.utcnow(),
    )
    response = auth_client.get(f"/api/tasks/{task.task_id}/result")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == TASK_FAILED
    assert "missing" in payload["error_message"].lower()
    assert payload["completed_at"]


def test_unknown_task_404(auth_client):
    assert auth_client.get("/api/tasks/does-not-exist").status_code == 404
    assert auth_client.get("/api/tasks/does-not-exist/result").status_code == 404


def test_duplicate_submission_reuses_active_task(app, monkeypatch):
    with app.app_context():
        from app.services.task_service import submit_inspection_analysis

        monkeypatch.setattr(
            "app.services.task_service.analysis_executor.submit",
            lambda *a, **k: None,
        )
        first = submit_inspection_analysis(55, 7)
        second = submit_inspection_analysis(55, 7)
        assert first["task_id"] == second["task_id"]
        assert second.get("reused") is True


def test_concurrent_task_submissions(app, monkeypatch):
    with app.app_context():
        from app.services.task_service import submit_inspection_analysis

        monkeypatch.setattr(
            "app.services.task_service.analysis_executor.submit",
            lambda *a, **k: None,
        )
        a = submit_inspection_analysis(1, allow_duplicate=True)
        b = submit_inspection_analysis(2, allow_duplicate=True)
        assert a["task_id"] != b["task_id"]
        assert a["status"] == TASK_PENDING
        assert b["status"] == TASK_PENDING


def test_task_store_active_lookup():
    store = task_store
    store.clear()
    active = store.create(inspection_id=99, media_id=1)
    store.update(active.task_id, status=TASK_RUNNING, started_at=datetime.utcnow())
    found = store.find_active(99, 1)
    assert found is not None
    assert found.status == TASK_RUNNING
    assert store.find_active(99, 2) is None
