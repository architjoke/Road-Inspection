"""SSIM recommends and is the required evidence; only admins make the final verification."""

from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
import pytest

from app import create_app
from app.services.repair_service import clear_repairs, create_repair, get_repair, update_repair


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    id = pk = 7
    municipality_id = 1
    full_name = "Test Admin"

    def __init__(self, role: str):
        self.role_name = self.dashboard_role_key = role

    def get_id(self):
        return "user:7"

    def has_role(self, *roles):
        return self.role_name in roles

    def is_super_admin(self):
        return self.role_name == "super_admin"


def _client(role: str):
    app = create_app({"TESTING": True, "OFFLINE_MODE": True, "WTF_CSRF_ENABLED": False,
                      "RATELIMIT_ENABLED": False})
    user = _User(role)

    @app.login_manager.user_loader
    def _load(_uid):
        return user

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:7"
        sess["_fresh"] = True
    return app, client


def _jpeg(img: np.ndarray) -> BytesIO:
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return BytesIO(buf.tobytes())


def _resurfacing_pair():
    rng = np.random.default_rng(11)
    top = cv2.GaussianBlur(rng.integers(30, 230, size=(140, 480), dtype=np.uint8), (5, 5), 0)
    cv2.rectangle(top, (30, 20), (110, 120), 240, -1)
    cv2.circle(top, (330, 70), 45, 25, -1)
    old_road = cv2.GaussianBlur(rng.integers(40, 160, size=(220, 480), dtype=np.uint8), (3, 3), 0)
    for cx, cy, r in ((120, 70, 35), (330, 120, 45), (220, 180, 30)):
        cv2.circle(old_road, (cx, cy), r, 15, -1)
    new_road = np.random.default_rng(99).integers(70, 95, size=(220, 480), dtype=np.uint8)
    for road in (old_road, new_road):
        cv2.line(road, (240, 0), (240, 220), 250, 6)
    to_bgr = lambda g: cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)  # noqa: E731
    return to_bgr(np.vstack([top, old_road])), to_bgr(np.vstack([top, new_road]))


@pytest.fixture(autouse=True)
def _fresh_repairs():
    clear_repairs()
    yield
    clear_repairs()


def _run_ssim(client, repair_id: int, after: np.ndarray | None = None):
    before, repaired = _resurfacing_pair()
    data = {
        "repair_id": str(repair_id),
        "before": (_jpeg(before), "before.jpg"),
        "after": (_jpeg(repaired if after is None else after), "after.jpg"),
    }
    return client.post("/api/verification/compare", data=data, content_type="multipart/form-data")


def test_ssim_recommends_but_never_verifies():
    app, client = _client("municipal_admin")
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        res = _run_ssim(client, repair["id"])
        assert res.status_code == 201
        body = res.get_json()
        assert body["ssim_recommendation"] in {"verify", "review"}
        assert "repair_status" not in body
        stored = get_repair(repair["id"])
        assert stored["status"] != "verified"
        assert stored["comparison_id"] == body["comparison_id"]
        assert stored["verification"]["status"] == body["status"]


def test_admin_cannot_verify_without_ssim_evidence():
    app, client = _client("municipal_admin")
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        res = client.post(f"/api/repairs/{repair['id']}/mark-verified", json={"note": "looks fine to me"})
        assert res.status_code == 400
        assert "Verify SSIM" in res.get_json()["error"]
        assert get_repair(repair["id"])["status"] != "verified"


def test_admin_verifies_on_ssim_evidence():
    app, client = _client("super_admin")
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        update_repair(repair["id"], comparison_id="cmp-1",
                      verification={"status": "Repair Verified", "manual_review_required": False})
        res = client.post(f"/api/repairs/{repair['id']}/mark-verified", json={})
        assert res.status_code == 200
        stored = get_repair(repair["id"])
        assert stored["status"] == "verified" and stored["payment_status"] == "pending"
        decision = stored["verification"]["final_decision"]
        assert decision["followed_ssim"] is True and decision["decided_by"] == 7
        assert decision["comparison_id"] == "cmp-1"


def test_overriding_ssim_needs_a_reason():
    app, client = _client("municipal_admin")
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        update_repair(repair["id"], comparison_id="cmp-2",
                      verification={"status": "No Change Detected - Manual Review Required",
                                    "manual_review_required": True})
        url = f"/api/repairs/{repair['id']}/mark-verified"
        assert client.post(url, json={}).status_code == 400
        assert client.post(url, json={"note": "short"}).status_code == 400
        ok = client.post(url, json={"note": "Inspected on site: whole lane resurfaced"})
        assert ok.status_code == 200
        decision = get_repair(repair["id"])["verification"]["final_decision"]
        assert decision["followed_ssim"] is False and "resurfaced" in decision["note"]


@pytest.mark.parametrize("role", ["field_officer", "viewer", "contractor", "field_worker"])
def test_only_admins_make_the_final_call(role):
    app, client = _client(role)
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        update_repair(repair["id"], comparison_id="cmp-3",
                      verification={"status": "Repair Verified", "manual_review_required": False})
        res = client.post(f"/api/repairs/{repair['id']}/mark-verified", json={})
        assert res.status_code in {302, 403}
        assert get_repair(repair["id"])["status"] != "verified"


def test_edit_form_cannot_bypass_verification():
    app, client = _client("municipal_admin")
    with app.app_context():
        repair = create_repair(damage_type="pothole")
        for form in ({"status": "verified"}, {"status": "paid"}, {"status": "closed"},
                     {"status": "open", "payment_status": "paid"}):
            client.post(f"/repairs/{repair['id']}", data=form)
            stored = get_repair(repair["id"])
            assert stored["status"] not in {"verified", "paid", "closed"}
            assert stored["payment_status"] != "paid"


def test_new_after_photo_invalidates_old_ssim_evidence(tmp_path, monkeypatch):
    from werkzeug.datastructures import FileStorage

    from app.services import workflow_service

    app, _ = _client("municipal_admin")
    with app.app_context():
        monkeypatch.setattr(workflow_service, "evidence_root", lambda: tmp_path)
        repair = create_repair(damage_type="pothole")
        update_repair(repair["id"], comparison_id="cmp-old",
                      verification={"status": "Repair Verified", "manual_review_required": False})
        _, after = _resurfacing_pair()
        fields = workflow_service.save_after_evidence(
            repair["id"], FileStorage(stream=_jpeg(after), filename="new_after.jpg")
        )
        update_repair(repair["id"], **fields)
        stored = get_repair(repair["id"])
        assert stored["comparison_id"] is None and not stored["verification"]
        with pytest.raises(workflow_service.VerificationDecisionError):
            workflow_service.mark_repair_verified(repair["id"], decided_by=7)


def test_standalone_photos_use_ai_damage_areas(tmp_path, monkeypatch):
    """No repair/inspection: the model finds the damage in the before photo."""
    from app.services.comparison_service import run_verification
    from app.services.workflow_service import ssim_recommendation

    monkeypatch.setenv("YOLO_FORCE_MOCK", "true")  # deterministic box in the road area
    app, _ = _client("municipal_admin")
    before, after = _resurfacing_pair()
    b, a = tmp_path / "b.jpg", tmp_path / "a.jpg"
    cv2.imwrite(str(b), before)
    cv2.imwrite(str(a), after)
    with app.app_context():
        repaired = run_verification(b, a, save_artifacts=False)
        assert repaired["damage_areas"] and repaired["damage_areas"][0]["required"] is True
        assert "no damage box available; whole-photo comparison only" not in repaired["review_reasons"]
        assert ssim_recommendation(repaired) == "verify"

        unchanged = run_verification(b, b, save_artifacts=False)
        assert ssim_recommendation(unchanged) == "review"
