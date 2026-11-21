"""Local end-to-end acceptance (Flask test client; no RDS/Aurora/internet)."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from PIL import Image

from app import create_app
from app.ai.model_loader import reset_model_cache
from app.services.offline_store import offline_store
from app.workers.task_store import TASK_SUCCEEDED, task_store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "road_sample.jpg"


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


def _ensure_fixture() -> Path:
    if FIXTURE.is_file():
        return FIXTURE
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color=(70, 70, 70)).save(FIXTURE, format="JPEG")
    return FIXTURE


def _make_app(*, force_mock: bool):
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "YOLO_FORCE_MOCK": force_mock,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "IMAGEKIT_PRIVATE_KEY": "",
            "IMAGEKIT_URL_ENDPOINT": "",
            "IMAGEKIT_PUBLIC_KEY": "",
        }
    )
    fake = _FakeUser()

    @application.login_manager.user_loader
    def _load(_uid):
        return fake

    return application


def _auth_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    return client


def _poll_until_done(client, task_id: str, timeout_sec: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_sec
    last = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/tasks/{task_id}")
        assert status.status_code == 200
        last = status.get_json()
        if last["status"] in {"SUCCEEDED", "FAILED"}:
            result = client.get(f"/api/tasks/{task_id}/result")
            assert result.status_code == 200
            return result.get_json()
        time.sleep(0.15)
    raise AssertionError(f"Task {task_id} did not finish; last={last}")


def _upload(client, image_path: Path, location_name: str = "E2E Road Sample"):
    data = {
        "file": (io.BytesIO(image_path.read_bytes()), "road_sample.jpg"),
        "location_name": location_name,
        "location_latitude": "16.7050",
        "location_longitude": "74.2431",
        "gps_manual_override": "1",
        "notes": "local e2e acceptance",
    }
    return client.post(
        "/api/inspections/upload",
        data=data,
        content_type="multipart/form-data",
    )


@pytest.fixture
def fixture_image():
    return _ensure_fixture()


def test_health_starts_without_remote_database():
    app = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": False,
        }
    )
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database_configured"] is False
    assert body.get("offline_mode") is True
    assert body.get("rds_contacted") is False
    assert "phase_14" in body.get("phase", "")
    assert "components" in body


def test_e2e_upload_analyze_poll_result_mock(fixture_image, monkeypatch):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    reset_model_cache()
    app = _make_app(force_mock=True)
    client = _auth_client(app)

    with app.app_context():
        response = _upload(client, fixture_image)
        assert response.status_code == 201, response.get_json()
        payload = response.get_json()
        assert payload["inspection_id"]
        assert payload["media_id"]
        assert payload["local_path"]
        assert Path(payload["local_path"]).is_file()
        assert payload["analysis_task"]
        task_id = payload["analysis_task"]["task_id"]

        # Duplicate analyze reuses active/same task family
        again = client.post(f"/api/inspections/{payload['inspection_id']}/analyze")
        assert again.status_code == 202
        again_body = again.get_json()
        # May reuse active or create after completion; if still active, must reuse
        if again_body.get("status") in {"PENDING", "RUNNING"}:
            assert again_body["task_id"] == task_id
            assert again_body.get("reused") is True

        result = _poll_until_done(client, task_id)
        assert result["status"] == TASK_SUCCEEDED
        summary = result["result_summary"]
        assert "detection_count" in summary
        assert isinstance(summary["detections"], list)
        for det in summary["detections"]:
            assert "class_name" in det or "damage_type" in det
            assert "confidence" in det
            assert "bbox_xyxy" in det
            assert len(det["bbox_xyxy"]) == 4
            assert "severity" in det

        annotated = summary.get("annotated_path")
        assert annotated
        annotated_path = Path(annotated)
        assert annotated_path.is_file()
        assert annotated_path.resolve() != Path(payload["local_path"]).resolve()
        assert "_annotated" in annotated_path.name

        # Missing inspection id → controlled 404
        missing = client.post("/api/inspections/999999/analyze")
        assert missing.status_code == 404
        assert "not found" in missing.get_json()["error"].lower()

        # Invalid image → controlled 400
        bad = client.post(
            "/api/inspections/upload",
            data={
                "file": (io.BytesIO(b"not-an-image"), "bad.txt"),
                "location_name": "Bad",
                "location_latitude": "16.7050",
                "location_longitude": "74.2431",
                "gps_manual_override": "1",
            },
            content_type="multipart/form-data",
        )
        assert bad.status_code == 400
        assert bad.get_json().get("error")


def test_e2e_real_model_cpu(fixture_image, monkeypatch):
    from app.ai.model_loader import default_model_path

    weights = default_model_path()
    if not weights.is_file():
        pytest.skip(f"Weights not found at {weights}")

    monkeypatch.setenv("YOLO_FORCE_MOCK", "false")
    monkeypatch.setenv("YOLO_DEVICE", "cpu")
    monkeypatch.setenv("YOLO_IMGSZ", "320")  # faster CPU smoke; avoids long hangs
    reset_model_cache()
    app = _make_app(force_mock=False)
    client = _auth_client(app)

    with app.app_context():
        from app.ai.model_loader import get_model_config

        cfg = get_model_config()
        assert cfg["force_mock"] is False

        response = _upload(client, fixture_image, location_name="E2E Real Model")
        assert response.status_code == 201, response.get_json()
        payload = response.get_json()
        task_id = payload["analysis_task"]["task_id"]
        result = _poll_until_done(client, task_id, timeout_sec=180.0)
        assert result["status"] == TASK_SUCCEEDED
        summary = result["result_summary"]
        assert isinstance(summary["detections"], list)
        annotated = Path(summary["annotated_path"])
        assert annotated.is_file()


def test_duplicate_analyze_reuses_before_completion(fixture_image, monkeypatch):
    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")
    reset_model_cache()
    app = _make_app(force_mock=True)
    client = _auth_client(app)

    with app.app_context():
        # Block executor so task stays PENDING/RUNNING
        submitted = []

        def _capture(fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

        monkeypatch.setattr(
            "app.services.task_service.analysis_executor.submit",
            _capture,
        )

        response = _upload(client, fixture_image)
        assert response.status_code == 201
        payload = response.get_json()
        first_id = payload["analysis_task"]["task_id"]
        assert payload["analysis_task"].get("reused") is False

        second = client.post(f"/api/inspections/{payload['inspection_id']}/analyze")
        assert second.status_code == 202
        body = second.get_json()
        assert body["task_id"] == first_id
        assert body.get("reused") is True
        assert len(submitted) == 1
