"""PC-upload realities: GPS source priority, video GPS, ward polygons, SSIM verification."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.ai.before_after_verifier import STATUS_VERIFIED, compare_before_after
from app.services.gps_service import (
    extract_iso6709_from_video,
    resolve_inspection_gps_details,
)
from app.services.ward_service import parse_boundary_geojson

# --- GPS priority -----------------------------------------------------------


def _jpeg_with_gps(path: Path, lat: float, lng: float) -> Path:
    def dms(value):
        value = abs(value)
        d = int(value)
        m = int((value - d) * 60)
        s = round(((value - d) * 60 - m) * 60, 2)
        return (float(d), float(m), s)

    exif = Image.Exif()
    gps = {1: "N", 2: dms(lat), 3: "E", 4: dms(lng)}
    exif[0x8825] = gps
    Image.new("RGB", (64, 48), (90, 90, 90)).save(path, exif=exif.tobytes())
    return path


def test_photo_gps_beats_pc_browser_location(tmp_path):
    photo = _jpeg_with_gps(tmp_path / "site.jpg", 16.6535, 74.2648)
    form = {  # office PC location, Wi-Fi estimate
        "location_latitude": "16.68783",
        "location_longitude": "74.21857",
        "gps_accuracy_meters": "500",
    }
    with open(photo, "rb") as fh:
        result = resolve_inspection_gps_details(form, fh, "image")
    assert result["source"] == "exif"
    assert float(result["latitude"]) == pytest.approx(16.6535, abs=1e-4)
    assert result["browser_distance_m"] > 5000
    assert result["needs_review"] is False


def test_coarse_browser_location_is_flagged():
    form = {"location_latitude": "16.68783", "location_longitude": "74.21857",
            "gps_accuracy_meters": "500"}
    result = resolve_inspection_gps_details(form, None, "image")
    assert result["source"] == "browser"
    assert result["needs_review"] is True
    assert any("500" in r for r in result["review_reasons"])


def test_city_level_browser_location_is_refused():
    form = {"location_latitude": "16.7318", "location_longitude": "73.9079",
            "gps_accuracy_meters": "100000"}
    with pytest.raises(ValueError, match="Pin the damage on the map"):
        resolve_inspection_gps_details(form, None, "image")


def test_manual_pin_wins_over_photo_gps(tmp_path):
    photo = _jpeg_with_gps(tmp_path / "site.jpg", 16.6535, 74.2648)
    form = {"location_latitude": "16.70000", "location_longitude": "74.20000",
            "gps_manual_override": "1"}
    with open(photo, "rb") as fh:
        result = resolve_inspection_gps_details(form, fh, "image")
    assert result["source"] == "manual"
    assert result["latitude"] == Decimal("16.70000")


def test_phone_gps_accepted_without_review():
    form = {"location_latitude": "16.6535", "location_longitude": "74.2648",
            "gps_accuracy_meters": "8"}
    assert resolve_inspection_gps_details(form, None, "image")["needs_review"] is False


def test_video_iso6709_gps(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 4096 + b"\xa9xyz\x00\x12\x15\xc7+16.6535+074.2648/" + b"\x00" * 64)
    gps = extract_iso6709_from_video(video)
    assert gps["source"] == "video_metadata"
    assert gps["latitude"] == pytest.approx(16.6535)
    assert gps["longitude"] == pytest.approx(74.2648)


# --- Ward polygons -------------------------------------------------------------


def test_boundary_geojson_accepts_feature_and_closes_ring():
    geo = parse_boundary_geojson(
        '{"type":"Feature","geometry":{"type":"Polygon",'
        '"coordinates":[[[74.2,16.6],[74.3,16.6],[74.3,16.7],[74.2,16.7]]]}}'
    )
    ring = geo["coordinates"][0]
    assert geo["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) == 5


def test_boundary_geojson_picks_largest_of_multipolygon():
    geo = parse_boundary_geojson(
        '{"type":"MultiPolygon","coordinates":['
        '[[[0,0],[0.001,0],[0.001,0.001],[0,0]]],'
        '[[[74.2,16.6],[74.3,16.6],[74.3,16.7],[74.2,16.6]]]]}'
    )
    assert geo["coordinates"][0][0] == [74.2, 16.6]


def test_boundary_geojson_rejects_garbage():
    with pytest.raises(ValueError):
        parse_boundary_geojson('{"type":"Point","coordinates":[74,16]}')


# --- Before/after verification --------------------------------------------------

ROI = (180, 150, 300, 230)


def _road(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.integers(60, 190, size=(360, 480), dtype=np.uint8)
    road = cv2.GaussianBlur(noise, (0, 0), 1.2)
    for _ in range(40):  # cracks / stones give ORB something to match
        x, y = rng.integers(0, 480), rng.integers(0, 360)
        cv2.circle(road, (int(x), int(y)), int(rng.integers(3, 9)), int(rng.integers(0, 255)), -1)
    return cv2.cvtColor(road, cv2.COLOR_GRAY2BGR)


def _camera(img: np.ndarray, seed: int, gain: float = 1.15, up: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = (src + rng.uniform(-0.03, 0.03, src.shape) * [w, h]).astype(np.float32)
    moved = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h),
                                borderMode=cv2.BORDER_REFLECT)
    moved = np.clip(moved.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return cv2.resize(moved, (w * up, h * up), interpolation=cv2.INTER_CUBIC)


def _patched(img: np.ndarray) -> np.ndarray:
    out = img.copy()
    x1, y1, x2, y2 = ROI
    rng = np.random.default_rng(9)
    out[y1:y2, x1:x2] = np.clip(55 + rng.normal(0, 4, out[y1:y2, x1:x2].shape), 0, 255)
    return out


def _auto(result) -> bool:
    return result["status"] == STATUS_VERIFIED and not result["manual_review_required"]


def _write(tmp_path, name, img):
    path = tmp_path / name
    cv2.imwrite(str(path), img)
    return path


def test_real_repair_with_camera_move_is_verified(tmp_path):
    before = _road()
    b = _write(tmp_path, "b.jpg", before)
    a = _write(tmp_path, "a.jpg", _camera(_patched(before), 1))
    result = compare_before_after(b, a, roi=ROI)
    assert _auto(result), result["review_reasons"]
    assert result["roi_change_vs_context"] >= 0.15


def test_unrepaired_with_lighting_and_camera_change_is_not_verified(tmp_path):
    before = _road()
    b = _write(tmp_path, "b.jpg", before)
    a = _write(tmp_path, "a.jpg", _camera(before, 2, gain=1.3))
    assert not _auto(compare_before_after(b, a, roi=ROI))


def test_closer_unrepaired_photo_is_not_verified(tmp_path):
    # Black warp borders used to read as "big change" -> Repair Verified.
    before = _road()
    h, w = before.shape[:2]
    closer = cv2.resize(before[60:h - 30, 80:w - 80], (w * 2, h * 2))
    b = _write(tmp_path, "b.jpg", before)
    a = _write(tmp_path, "a.jpg", closer)
    whole = compare_before_after(b, a)
    assert not _auto(whole)
    assert "no damage box available; whole-photo comparison only" in whole["review_reasons"]
    assert not _auto(compare_before_after(b, a, roi=ROI))


def test_phone_sized_photos_are_downscaled(tmp_path):
    before = cv2.resize(_road(), (4000, 3000))
    b = _write(tmp_path, "b.jpg", before)
    a = _write(tmp_path, "a.jpg", before)
    result = compare_before_after(b, a, roi=(1500, 1250, 2500, 1900))
    assert result["working_scale"] < 0.5
    assert not _auto(result)


# --- Pages wire the new map tools ----------------------------------------------


def test_upload_and_ward_pages_include_map_tools():
    from tests.integration.test_product_pages import _FakeUser, _app, _client

    class _Admin(_FakeUser):
        def is_field_worker(self):
            return False

        def is_municipal_admin(self):
            return True

        def is_field_officer(self):
            return False

    app = _app()
    admin = _Admin()
    app.login_manager.user_loader(lambda _uid: admin)
    client = _client(app)
    upload = client.get("/inspections/new").get_data(as_text=True)
    assert 'id="gps-pick-map"' in upload
    assert "js/location_picker.js" in upload and "leaflet" in upload
    ward_form = client.get("/wards/new").get_data(as_text=True)
    assert 'id="boundary-map"' in ward_form and 'name="boundary_geojson"' in ward_form
    assert "js/ward_boundary_editor.js" in ward_form
    wards = client.get("/wards")
    assert wards.status_code == 200


def test_upload_resolution_floor():
    from io import BytesIO

    from werkzeug.datastructures import FileStorage

    from app.services.validation_service import check_image_resolution

    def upload(size):
        buf = BytesIO()
        Image.new("RGB", size, (90, 90, 90)).save(buf, "JPEG")
        buf.seek(0)
        return FileStorage(stream=buf, filename="x.jpg", content_type="image/jpeg")

    tiny = check_image_resolution(upload((180, 168)))
    assert tiny["valid"] is False and "too small" in tiny["error"]
    webcam = check_image_resolution(upload((640, 480)))
    assert webcam["valid"] is True and webcam["warning"]
    phone = upload((1600, 1200))
    assert check_image_resolution(phone)["warning"] is None
    assert phone.stream.tell() == 0  # stream rewound for the real save
