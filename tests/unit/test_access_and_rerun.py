"""Access scoping, re-analysis replacement and repair status mapping."""

from __future__ import annotations

import pytest

from app import create_app
from app.services import repair_persistence
from app.services.access_service import can_view_inspection, filter_rows_by_inspection
from app.services.detection_service import (
    discard_unreviewed,
    list_memory_detections,
    save_detections_memory,
    update_memory_detection,
)
from app.services.offline_store import offline_store
from app.services.repair_service import create_repair, list_repairs


class _User:
    def __init__(self, pk, role, municipality_id=1):
        self.pk = self.id = pk
        self.municipality_id = municipality_id
        self._role = role

    def is_super_admin(self):
        return self._role == "super_admin"

    def is_field_worker(self):
        return self._role == "field_worker"

    def is_contractor(self):
        return self._role == "contractor"


@pytest.fixture
def app_ctx():
    app = create_app({"TESTING": True, "OFFLINE_MODE": True, "WTF_CSRF_ENABLED": False})
    with app.app_context():
        yield app


def _inspection(municipality_id, user_id):
    return offline_store.create_inspection(
        inspection_code=f"T-{municipality_id}-{user_id}",
        municipality_id=municipality_id,
        user_id=user_id,
    )


def test_inspection_visibility_by_role(app_ctx):
    mine = _inspection(1, user_id=7)
    other_city = _inspection(2, user_id=99)
    admin = _User(1, "municipal_admin", municipality_id=1)
    worker = _User(7, "field_worker")
    contractor = _User(8, "contractor")
    root = _User(4, "super_admin", municipality_id=None)

    assert can_view_inspection(mine, admin) and not can_view_inspection(other_city, admin)
    assert can_view_inspection(mine, worker) and not can_view_inspection(other_city, worker)
    assert can_view_inspection(other_city, root)
    assert not can_view_inspection(mine, contractor)
    create_repair(inspection_id=mine.id, assignee_id=8)
    assert can_view_inspection(mine, contractor)


def test_detection_rows_filtered_to_visible_inspections(app_ctx):
    mine = _inspection(1, user_id=7)
    other_city = _inspection(2, user_id=99)
    rows = [{"inspection_id": mine.id, "detection_id": 1},
            {"inspection_id": other_city.id, "detection_id": 2}]
    visible = filter_rows_by_inspection(rows, _User(1, "municipal_admin", municipality_id=1))
    assert [r["detection_id"] for r in visible] == [1]


def _det():
    return {"class_name": "D40_pothole", "damage_type": "pothole", "confidence": 0.8,
            "bbox_xyxy": [1, 2, 30, 40], "status": "PENDING_REVIEW"}


def test_rerun_replaces_only_unreviewed_results(app_ctx):
    rows = save_detections_memory(5, 9, [_det(), _det(), _det()])
    reviewed, untouched, assigned = (r["detection_id"] for r in rows)
    update_memory_detection(reviewed, status="APPROVED", review_decision="approved")
    create_repair(inspection_id=5, detection_id=untouched)                 # open, unassigned
    create_repair(inspection_id=5, detection_id=assigned, assignee_id=8)   # already assigned

    removed = discard_unreviewed(5, 9)

    assert set(removed) == {untouched, assigned}
    left = {r["detection_id"] for r in list_memory_detections(5)}
    assert left == {reviewed}
    jobs = {r["detection_id"] for r in list_repairs()}
    assert untouched not in jobs          # untouched auto job dropped
    assert assigned in jobs               # assigned job kept for the contractor


def test_repair_status_mapping_fits_db_constraint():
    allowed = {"pending", "assigned", "in_progress", "completed", "cancelled", "reopened"}
    app_statuses = {"open", "assigned", "in_progress", "evidence_uploaded",
                    "verified", "closed", "paid", "rejected"}
    assert app_statuses <= set(repair_persistence.STATUS_TO_DB)
    assert set(repair_persistence.STATUS_TO_DB.values()) <= allowed
    assert set(repair_persistence.DB_TO_STATUS) <= allowed


def test_persistence_disabled_offline(app_ctx):
    assert repair_persistence.enabled() is False
    assert repair_persistence.save({"inspection_id": 1}) is None
