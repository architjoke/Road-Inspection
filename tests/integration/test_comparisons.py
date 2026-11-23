"""Phase 12 before/after verification tests (OpenCV SSIM; no RDS)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import create_app
from app.ai.before_after_verifier import (
    STATUS_NO_CHANGE,
    STATUS_PARTIAL,
    STATUS_VERIFIED,
    align_images,
    classify_ssim,
    compare_before_after,
    compute_ssim,
)
from app.services.comparison_service import get_comparison, run_verification


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
def app():
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

    yield application


@pytest.fixture
def auth_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "user:1"
        sess["_fresh"] = True
    return client


def _save_bgr(path: Path, image: np.ndarray) -> Path:
    cv2.imwrite(str(path), image)
    return path


def _textured_scene(seed: int = 0, shift: tuple[int, int] = (0, 0)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(40, 200, size=(360, 480), dtype=np.uint8)
    cv2.rectangle(base, (40, 40), (140, 140), 255, -1)
    cv2.rectangle(base, (300, 200), (420, 300), 20, -1)
    cv2.circle(base, (240, 120), 40, 180, -1)
    dx, dy = shift
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    warped = cv2.warpAffine(base, m, (480, 360), borderMode=cv2.BORDER_REFLECT)
    return cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)


def test_ssim_identical_high(tmp_path):
    img = _textured_scene(1)
    a = _save_bgr(tmp_path / "a.jpg", img)
    b = _save_bgr(tmp_path / "b.jpg", img.copy())
    score = compute_ssim(cv2.imread(str(a)), cv2.imread(str(b)))
    assert score > 0.95


def test_ssim_changed_lower(tmp_path):
    before = _textured_scene(2)
    after = before.copy()
    cv2.rectangle(after, (50, 50), (200, 200), (0, 0, 0), -1)
    score = compute_ssim(before, after)
    assert score < 0.95


def test_classify_rules():
    assert classify_ssim(0.40)["status"] == STATUS_VERIFIED
    assert classify_ssim(0.40)["manual_review_required"] is False
    partial = classify_ssim(0.70)
    assert partial["status"] == STATUS_PARTIAL
    assert partial["manual_review_required"] is True
    none = classify_ssim(0.92)
    assert none["status"] == STATUS_NO_CHANGE
    assert none["manual_review_required"] is True


def test_alignment_homography_or_fallback():
    before = _textured_scene(3)
    after = _textured_scene(3, shift=(12, -8))
    cv2.rectangle(after, (60, 60), (160, 160), (10, 10, 10), -1)
    result = align_images(before, after)
    assert result.aligned_after.shape[:2] == before.shape[:2]
    base = result.method.removesuffix("+ecc")
    assert base in {"sift_homography", "sift_affine", "orb_homography", "orb_affine",
                    "ecc_same_viewpoint", "resize_fallback"}
    assert result.match_count >= 0


def test_alignment_fallback_on_blank():
    before = np.full((200, 200, 3), 128, dtype=np.uint8)
    after = np.full((180, 220, 3), 130, dtype=np.uint8)
    result = align_images(before, after)
    assert result.method == "resize_fallback"
    assert result.success is False
    assert result.aligned_after.shape[:2] == before.shape[:2]


def test_compare_verified_partial_nochange(tmp_path):
    before = _textured_scene(5)
    # Same road, localized repair patch (keeps enough shared features to align)
    after_changed = before.copy()
    cv2.rectangle(after_changed, (80, 60), (280, 220), 20, -1)
    p1 = _save_bgr(tmp_path / "b1.jpg", before)
    p2 = _save_bgr(tmp_path / "a1.jpg", after_changed)
    verified = compare_before_after(p1, p2, output_dir=tmp_path / "out1")
    assert "status" in verified
    assert Path(verified["aligned_after_path"]).is_file()

    p3 = _save_bgr(tmp_path / "b2.jpg", before)
    p4 = _save_bgr(tmp_path / "a2.jpg", before.copy())
    same = compare_before_after(p3, p4, output_dir=tmp_path / "out2")
    assert same["ssim"] > 0.85
    assert same["status"] == STATUS_NO_CHANGE
    assert same["manual_review_required"] is True


def test_different_roads_raise_error(tmp_path):
    from app.ai.before_after_verifier import VerificationError

    # Distinct layouts (not just different noise) — must not score as a repair.
    road_a = np.full((360, 480, 3), 80, dtype=np.uint8)
    cv2.rectangle(road_a, (20, 40), (200, 300), (220, 220, 220), -1)
    cv2.line(road_a, (0, 300), (480, 300), (255, 255, 255), 4)
    road_b = np.full((360, 480, 3), 80, dtype=np.uint8)
    cv2.circle(road_b, (360, 120), 90, (30, 30, 30), -1)
    cv2.line(road_b, (0, 80), (480, 80), (200, 200, 200), 4)
    p1 = _save_bgr(tmp_path / "road_a.jpg", road_a)
    p2 = _save_bgr(tmp_path / "road_b.jpg", road_b)
    with pytest.raises(VerificationError, match="same road|different|mismatch|too low"):
        compare_before_after(p1, p2, output_dir=tmp_path / "out_mismatch")


def test_boundary_manual_review_flag():
    near = classify_ssim(0.60)
    assert near["manual_review_required"] is True


def test_run_verification_persists(tmp_path, app):
    before = _textured_scene(7)
    after = before.copy()
    cv2.circle(after, (200, 150), 60, (0, 0, 0), -1)
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", after)
    with app.app_context():
        record = run_verification(b, a, inspection_id=11)
        assert record["comparison_id"]
        assert get_comparison(record["comparison_id"]) is not None
        assert record["inspection_id"] == 11
        assert "completion_percentage" in record


def test_api_verification_compare_and_get(auth_client, tmp_path, app):
    before = _textured_scene(9)
    after = before.copy()
    cv2.rectangle(after, (30, 30), (250, 250), (5, 5, 5), -1)
    b = _save_bgr(tmp_path / "api_before.jpg", before)
    a = _save_bgr(tmp_path / "api_after.jpg", after)

    with app.app_context():
        response = auth_client.post(
            "/api/verification/compare",
            data={
                "before_path": str(b),
                "after_path": str(a),
                "inspection_id": "55",
            },
        )
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["ssim"] is not None
    assert body["status"]
    assert body["manual_review_required"] in {True, False}

    detail = auth_client.get(f"/api/verification/{body['comparison_id']}")
    assert detail.status_code == 200
    assert detail.get_json()["comparison_id"] == body["comparison_id"]

    listing = auth_client.get("/api/verification?inspection_id=55")
    assert listing.status_code == 200
    assert listing.get_json()["count"] >= 1

    missing = auth_client.get("/api/verification/does-not-exist")
    assert missing.status_code == 404


def test_api_multipart_compare(auth_client, tmp_path):
    before = _textured_scene(11)
    after = before.copy()
    after[:] = (after.astype(np.int16) * 0.3).clip(0, 255).astype(np.uint8)
    b = _save_bgr(tmp_path / "mb.jpg", before)
    a = _save_bgr(tmp_path / "ma.jpg", after)

    before_buf = BytesIO(b.read_bytes())
    after_buf = BytesIO(a.read_bytes())
    response = auth_client.post(
        "/api/verification/compare",
        data={
            "before": (before_buf, "before.jpg"),
            "after": (after_buf, "after.jpg"),
            "inspection_id": "3",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    assert "comparison_id" in response.get_json()


def test_task_result_includes_verification_when_linked(tmp_path, app):
    """Additive verification summary on task result when inspection has comparisons."""
    from app.services.offline_store import offline_store
    from app.services.task_service import get_task_result
    from app.workers.task_store import TASK_SUCCEEDED, task_store

    before = _textured_scene(13)
    after = before.copy()
    cv2.rectangle(after, (10, 10), (300, 280), 0, -1)
    b = _save_bgr(tmp_path / "tb.jpg", before)
    a = _save_bgr(tmp_path / "ta.jpg", after)

    with app.app_context():
        insp = offline_store.create_inspection(
            inspection_code="INS-VERIFY-1",
            municipality_id=1,
            user_id=1,
            location_name="verify-link",
            location_latitude=16.7,
            location_longitude=74.2,
        )
        insp_id = insp.id
        run_verification(b, a, inspection_id=insp_id)
        task = task_store.create(
            task_type="inspection_analysis",
            inspection_id=insp_id,
        )
        task_store.update(
            task.task_id,
            status=TASK_SUCCEEDED,
            progress=100.0,
            result_summary={"detection_count": 0},
        )

        payload = get_task_result(task.task_id)
        assert payload is not None
        assert "verification" in payload
        assert payload["verification"]["count"] >= 1
        assert "completion_percentage" in payload["verification"]


def _resurfacing_pair() -> tuple[np.ndarray, np.ndarray]:
    """Same viewpoint: stable verge/trees on top, the whole road re-laid below."""
    rng = np.random.default_rng(11)
    top = cv2.GaussianBlur(rng.integers(30, 230, size=(140, 480), dtype=np.uint8), (5, 5), 0)
    cv2.rectangle(top, (30, 20), (110, 120), 240, -1)
    cv2.circle(top, (330, 70), 45, 25, -1)
    old_road = cv2.GaussianBlur(rng.integers(40, 160, size=(220, 480), dtype=np.uint8), (3, 3), 0)
    for cx, cy, r in ((120, 70, 35), (330, 120, 45), (220, 180, 30)):  # potholes
        cv2.circle(old_road, (cx, cy), r, 15, -1)
    new_road = np.random.default_rng(99).integers(70, 95, size=(220, 480), dtype=np.uint8)  # fresh asphalt
    for road in (old_road, new_road):
        cv2.line(road, (240, 0), (240, 220), 250, 6)  # centre line survives
    before = cv2.cvtColor(np.vstack([top, old_road]), cv2.COLOR_GRAY2BGR)
    after = cv2.cvtColor(np.vstack([top, new_road]), cv2.COLOR_GRAY2BGR)
    return before, after


def test_full_resurfacing_is_verified(tmp_path):
    """Whole road re-laid: the ring around the pothole changes too — still a repair."""
    before, after = _resurfacing_pair()
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", after)
    roi = (290, 140 + 75, 370, 140 + 165)  # pothole at (330, 120) in road coordinates
    result = compare_before_after(b, a, output_dir=tmp_path / "out", roi=roi)
    assert result["alignment"]["success"] is True
    assert result["overlap_ratio"] >= 0.6
    assert result["status"] == STATUS_VERIFIED
    assert result["manual_review_required"] is False
    assert any("resurfaced" in n for n in result["notes"])


def test_lighting_change_without_repair_is_not_verified(tmp_path):
    before, _ = _resurfacing_pair()
    darker = np.clip(before.astype(np.float32) * 0.55, 0, 255).astype(np.uint8)
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", darker)
    result = compare_before_after(b, a, roi=(290, 215, 370, 305))
    assert result["status"] != STATUS_VERIFIED or result["manual_review_required"] is True


def _view(img: np.ndarray, rot: float = 0.0, zoom: float = 1.0, tilt: float = 0.0) -> np.ndarray:
    """Re-photograph the scene from another angle (rotation, distance, phone pitch)."""
    h, w = img.shape[:2]
    m = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), rot, zoom), [0, 0, 1]])
    k = tilt * w
    keystone = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32([[k, 0], [w - k, 0], [w, h], [0, h]])
    )
    return cv2.warpPerspective(img, keystone @ m, (w, h), borderMode=cv2.BORDER_REFLECT)


@pytest.mark.parametrize("angle", [dict(rot=7), dict(zoom=0.85), dict(zoom=1.15), dict(tilt=0.1),
                                   dict(rot=-6, zoom=0.9, tilt=0.06)])
def test_resurfacing_verified_from_another_angle(tmp_path, angle):
    before, after = _resurfacing_pair()
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", _view(after, **angle))
    result = compare_before_after(b, a, roi=(290, 215, 370, 305))
    assert result["alignment"]["success"] is True
    assert result["status"] == STATUS_VERIFIED and result["manual_review_required"] is False


@pytest.mark.parametrize("angle", [dict(rot=7), dict(zoom=0.85), dict(tilt=0.1)])
def test_unrepaired_road_from_another_angle_is_not_verified(tmp_path, angle):
    before, _ = _resurfacing_pair()
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", _view(before, **angle))
    result = compare_before_after(b, a, roi=(290, 215, 370, 305))
    assert result["status"] != STATUS_VERIFIED or result["manual_review_required"] is True


def test_mirrored_photo_is_not_accepted_as_same_scene(tmp_path):
    from app.ai.before_after_verifier import VerificationError

    before, after = _resurfacing_pair()
    b = _save_bgr(tmp_path / "before.jpg", before)
    a = _save_bgr(tmp_path / "after.jpg", cv2.flip(after, 1))
    try:
        result = compare_before_after(b, a, roi=(290, 215, 370, 305))
    except VerificationError:
        return
    assert result["status"] != STATUS_VERIFIED or result["manual_review_required"] is True
