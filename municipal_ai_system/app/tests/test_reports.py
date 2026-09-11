"""Phase 13 multi-format report exporter tests (offline; no RDS)."""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import pytest

from app import create_app
from app.ai.severity_engine import enrich_detections
from app.services.comparison_service import run_verification
from app.services.detection_service import save_detections_memory
from app.services.offline_store import offline_store
from app.services.report_service import (
    compile_inspection_report,
    export_csv_bytes,
    export_excel_bytes,
    export_json_bytes,
    export_pdf_bytes,
    write_report_file,
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


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "REPORTS_OUTPUT_ROOT": tmp_path / "generated_reports",
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


def _seed_inspection_with_detections(*, with_verification: bool = False, tmp_path=None):
    inspection = offline_store.create_inspection(
        inspection_code="INS-RPT-1",
        municipality_id=1,
        user_id=1,
        ward_id=3,
        location_name="Report Test Road",
        location_latitude=16.705,
        location_longitude=74.2431,
        status="pending_review",
    )
    offline_store.add_media(
        inspection.id,
        media_type="image",
        file_path=str((tmp_path / "road.jpg") if tmp_path else "/tmp/road.jpg"),
        file_name="road.jpg",
    )
    dets = enrich_detections(
        [
            {
                "class_id": 3,
                "class_name": "D40_pothole",
                "damage_type": "pothole",
                "confidence": 0.91,
                "bbox_xyxy": [10, 10, 120, 90],
                "source_width": 640,
                "source_height": 480,
                "model_version": "mock",
            },
            {
                "class_id": 0,
                "class_name": "D00_longitudinal",
                "damage_type": "longitudinal_crack",
                "confidence": 0.55,
                "bbox_xyxy": [200, 40, 400, 80],
                "source_width": 640,
                "source_height": 480,
                "model_version": "mock",
            },
        ]
    )
    save_detections_memory(inspection.id, 1, dets)

    if with_verification and tmp_path is not None:
        import cv2
        import numpy as np

        before = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(before, (20, 20), (120, 120), (200, 200, 200), -1)
        after = before.copy()
        cv2.rectangle(after, (20, 20), (120, 120), (10, 10, 10), -1)
        b = tmp_path / "before.jpg"
        a = tmp_path / "after.jpg"
        cv2.imwrite(str(b), before)
        cv2.imwrite(str(a), after)
        run_verification(b, a, inspection_id=inspection.id)

    return inspection


def test_compile_empty_detections(app):
    with app.app_context():
        insp = offline_store.create_inspection(
            inspection_code="INS-EMPTY",
            municipality_id=1,
            user_id=1,
            location_name="Empty",
            location_latitude=1.0,
            location_longitude=2.0,
        )
        report = compile_inspection_report(insp.id)
        assert report["executive_summary"]["total_detections"] == 0
        assert report["detailed_findings"] == []
        assert report["verification"]["count"] == 0
        assert report["notes"]["rds_contacted"] is False


def test_compile_with_severity_gps_verification(app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(
            with_verification=True, tmp_path=tmp_path
        )
        report = compile_inspection_report(insp.id)
        assert report["header"]["inspection_id"] == insp.id
        assert report["header"]["latitude"] == pytest.approx(16.705)
        assert report["executive_summary"]["total_detections"] == 2
        assert sum(report["executive_summary"]["severity_breakdown"].values()) == 2
        assert report["executive_summary"]["estimated_total_repair_cost"] > 0
        assert len(report["detailed_findings"]) == 2
        assert report["verification"]["count"] >= 1
        assert report["recommendations"]
        assert any(r.get("timeline") for r in report["recommendations"])


def test_pdf_generation(app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(tmp_path=tmp_path)
        report = compile_inspection_report(insp.id)
        pdf = export_pdf_bytes(report)
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 500


def test_csv_and_json_export(app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(tmp_path=tmp_path)
        report = compile_inspection_report(insp.id)
        raw = export_json_bytes(report)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["report_title"]
        assert "detailed_findings" in payload

        csv_bytes = export_csv_bytes(report)
        text = csv_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text.split("\n\n")[0]))
        rows = list(reader)
        assert reader.fieldnames is not None
        assert "detection_id" in reader.fieldnames
        assert "severity" in reader.fieldnames
        assert len(rows) == 2


def test_excel_export(app, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    with app.app_context():
        insp = _seed_inspection_with_detections(tmp_path=tmp_path)
        report = compile_inspection_report(insp.id)
        xlsx = export_excel_bytes(report)
        assert xlsx[:2] == b"PK"
        wb = openpyxl.load_workbook(io.BytesIO(xlsx))
        assert "Summary" in wb.sheetnames
        assert "Findings" in wb.sheetnames


def test_write_report_file(app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(tmp_path=tmp_path)
        meta = write_report_file(insp.id, "pdf")
        assert Path(meta["path"]).is_file()
        assert meta["size_bytes"] > 0


def test_api_report_endpoints(auth_client, app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(
            with_verification=True, tmp_path=tmp_path
        )
        insp_id = insp.id

    pdf = auth_client.get(f"/api/reports/{insp_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.data[:4] == b"%PDF"
    assert "pdf" in (pdf.mimetype or "")

    js = auth_client.get(f"/api/reports/{insp_id}/json")
    assert js.status_code == 200
    body = json.loads(js.data)
    assert body["executive_summary"]["total_detections"] == 2
    assert body["header"]["latitude"] == pytest.approx(16.705)

    csv_resp = auth_client.get(f"/api/reports/{insp_id}/csv")
    assert csv_resp.status_code == 200
    assert b"detection_id" in csv_resp.data

    xlsx = auth_client.get(f"/api/reports/{insp_id}/excel")
    assert xlsx.status_code == 200
    assert xlsx.data[:2] == b"PK"

    missing = auth_client.get("/api/reports/999999/pdf")
    assert missing.status_code == 404


def test_api_async_export_job(auth_client, app, tmp_path):
    with app.app_context():
        insp = _seed_inspection_with_detections(tmp_path=tmp_path)
        insp_id = insp.id

    response = auth_client.post(
        "/api/reports/export",
        json={"inspection_id": insp_id, "format": "json", "async": True},
    )
    assert response.status_code == 202, response.get_json()
    job_id = response.get_json()["job_id"]

    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        status = auth_client.get(f"/api/reports/jobs/{job_id}").get_json()
        if status.get("status") in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.1)
    assert status is not None
    assert status["status"] == "SUCCEEDED"
    assert status["result"]["path"]


def test_reports_page_renders(auth_client, app, tmp_path):
    with app.app_context():
        _seed_inspection_with_detections(tmp_path=tmp_path)
    page = auth_client.get("/reports")
    assert page.status_code == 200
    assert b"Download PDF" in page.data
