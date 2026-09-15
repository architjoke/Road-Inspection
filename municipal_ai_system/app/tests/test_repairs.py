"""Repair workflow smoke tests."""

from app.services.repair_service import create_repair, list_repairs, update_repair


def test_repair_lifecycle():
    job = create_repair(location="Ward 4", damage_type="pothole", assignee_id=9)
    assert job["status"] == "assigned"
    updated = update_repair(job["id"], status="in_progress")
    assert updated["status"] == "in_progress"
    rows = list_repairs(assignee_id=9)
    assert len(rows) == 1
