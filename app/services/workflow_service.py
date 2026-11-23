"""Cross-role workflow helpers: inspection → repair → contractor → verify → pay."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


def _user_is_contractor(user) -> bool:
    blobs = [
        getattr(user, "role_name", None),
        getattr(user, "display_role_name", None),
        getattr(user, "dashboard_role_key", None),
    ]
    for blob in blobs:
        if blob and "contractor" in str(blob).lower():
            return True
    has_role = getattr(user, "has_role", None)
    if callable(has_role):
        try:
            return bool(has_role("contractor"))
        except Exception:
            return False
    return False


def list_contractor_users(municipality_id: int | None = None) -> list[Any]:
    from app.services.directory_service import list_users

    return [
        u
        for u in list_users(municipality_id=municipality_id)
        if _user_is_contractor(u)
    ]


def resolve_assignee_name(assignee_id: int | None) -> str | None:
    if not assignee_id:
        return None
    try:
        from app.services.directory_service import get_user

        user = get_user(assignee_id)
        if user is None:
            return None
        return getattr(user, "full_name", None) or getattr(user, "username", None)
    except Exception:
        logger.exception("Could not resolve assignee name for %s", assignee_id)
        return None


def get_inspection_by_id(inspection_id: int | None):
    if not inspection_id:
        return None
    try:
        from app.services.offline_store import offline_mode_enabled, offline_store

        if offline_mode_enabled():
            return offline_store.get_inspection(int(inspection_id))
    except Exception:
        pass
    try:
        from app.extensions import db
        from app.models.inspection import Inspection

        return db.session.get(Inspection, int(inspection_id))
    except Exception:
        logger.exception("Could not load inspection %s", inspection_id)
        return None


def inspection_context(inspection_id: int | None) -> dict[str, Any]:
    """Pull location + GPS from an inspection for repair handoff."""
    inspection = get_inspection_by_id(inspection_id)
    if inspection is None:
        return {}
    lat = getattr(inspection, "location_latitude", None)
    lng = getattr(inspection, "location_longitude", None)
    try:
        lat = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        lat = None
    try:
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lng = None
    return {
        "location": getattr(inspection, "location_name", None) or None,
        "latitude": lat,
        "longitude": lng,
    }


def create_linked_repair(
    *,
    inspection_id: int | None = None,
    detection_id: int | None = None,
    location: str | None = None,
    damage_type: str | None = None,
    severity: str | None = None,
    assignee_id: int | None = None,
    assignee_name: str | None = None,
    notes: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
):
    """Create a repair and fill gaps from the linked inspection + assignee."""
    from app.services.repair_service import create_repair

    ctx = inspection_context(inspection_id)
    name = assignee_name or resolve_assignee_name(assignee_id)
    return create_repair(
        inspection_id=inspection_id,
        detection_id=detection_id,
        location=location or ctx.get("location"),
        damage_type=damage_type,
        severity=severity,
        assignee_id=assignee_id,
        assignee_name=name,
        notes=notes,
        latitude=latitude if latitude is not None else ctx.get("latitude"),
        longitude=longitude if longitude is not None else ctx.get("longitude"),
    )


def evidence_root() -> Path:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            root = Path(current_app.config.get("TEMP_UPLOAD_ROOT", "tmp"))
        else:
            root = Path("tmp")
    except Exception:
        root = Path("tmp")
    out = root / "repair_evidence"
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_after_evidence(repair_id: int, uploaded: FileStorage | None) -> dict[str, Any]:
    """Persist after-repair image to disk and return fields for update_repair."""
    if uploaded is None or not uploaded.filename:
        return {}
    safe = secure_filename(uploaded.filename) or "after.jpg"
    suffix = Path(safe).suffix or ".jpg"
    dest = evidence_root() / f"repair_{repair_id}_after{suffix}"
    uploaded.save(dest)
    return {
        "evidence_filename": safe,
        "evidence_path": str(dest),
        "status": "evidence_uploaded",
        # A new after photo makes any earlier SSIM result stale: it must be re-run
        # before an admin can verify (the decision rests on this evidence).
        "comparison_id": None,
        "verification": None,
    }


# Roles allowed to make the final verification decision. The SSIM comparison only
# recommends; a person in one of these roles always makes the call.
FINAL_VERIFIER_ROLES = ("super_admin", "municipal_admin")
SSIM_RECOMMENDS_VERIFY = "Repair Verified"
MIN_OVERRIDE_NOTE = 10


class VerificationDecisionError(ValueError):
    """The final verification was refused (no SSIM run yet, or a reason is missing)."""


def ssim_recommendation(verification: dict | None) -> str | None:
    """'verify' when SSIM found a clear repair, 'review' otherwise, None if not run."""
    if not verification or not verification.get("status"):
        return None
    clear = (
        verification.get("status") == SSIM_RECOMMENDS_VERIFY
        and not verification.get("manual_review_required")
    )
    return "verify" if clear else "review"


def mark_repair_verified(
    repair_id: int,
    *,
    decided_by: int | str | None,
    decided_by_name: str | None = None,
    note: str | None = None,
) -> dict | None:
    """
    Final, human verification of a repair (status -> verified, payment -> pending).

    Allowed only after Verify SSIM has been run for this repair. If the admin goes
    against the SSIM recommendation, a written reason is required. The decision is
    stored together with the SSIM evidence it was based on.
    """
    from datetime import datetime

    from app.services.repair_service import get_repair, update_repair

    repair = get_repair(repair_id)
    if repair is None:
        return None
    verification = dict(repair.get("verification") or {})
    recommendation = ssim_recommendation(verification)
    if recommendation is None or not repair.get("comparison_id"):
        raise VerificationDecisionError(
            "Run Verify SSIM for this repair first; the final decision needs the "
            "before/after comparison."
        )
    note = (note or "").strip()
    if recommendation != "verify" and len(note) < MIN_OVERRIDE_NOTE:
        raise VerificationDecisionError(
            f"SSIM did not recommend verification ({verification.get('status')}). "
            f"Give a reason of at least {MIN_OVERRIDE_NOTE} characters to verify anyway."
        )
    verification["final_decision"] = {
        "decision": "verified",
        "decided_by": decided_by,
        "decided_by_name": decided_by_name,
        "decided_at": datetime.utcnow().isoformat() + "Z",
        "note": note or None,
        "ssim_recommendation": recommendation,
        "followed_ssim": recommendation == "verify",
        "comparison_id": repair.get("comparison_id"),
    }
    return update_repair(
        repair_id, status="verified", payment_status="pending", verification=verification
    )


def auto_create_repairs_from_detections(
    *,
    inspection_id: int,
    detections: list[dict[str, Any]],
    memory_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Create unassigned repair jobs for each detection so municipal staff can assign.

    Prefer potholes and severe damages, but create for all YOLO damage classes.
    Skips detections that already have a linked repair.
    """
    from app.services.repair_service import list_repairs

    memory_rows = memory_rows or []
    existing = {
        r.get("detection_id")
        for r in list_repairs()
        if r.get("detection_id") is not None and r.get("inspection_id") == inspection_id
    }
    created: list[dict[str, Any]] = []

    # Pair memory rows (with detection_id) to raw detections by index when lengths match
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    if memory_rows and len(memory_rows) == len(detections):
        pairs = list(zip(memory_rows, detections))
    elif memory_rows:
        pairs = [(m, None) for m in memory_rows]
    else:
        pairs = [({}, d) for d in detections]

    for mem, det in pairs:
        source = det or mem
        det_id = mem.get("detection_id")
        if det_id is not None and det_id in existing:
            continue
        damage = (
            source.get("damage_type")
            or source.get("class_name")
            or mem.get("damage_type")
            or mem.get("class_name")
            or "road_damage"
        )
        severity = source.get("severity") or mem.get("severity") or "MODERATE"
        conf = source.get("confidence") or mem.get("confidence")
        notes = f"Auto-created from AI detection (confidence {conf}). Assign a contractor."
        repair = create_linked_repair(
            inspection_id=inspection_id,
            detection_id=int(det_id) if det_id is not None else None,
            damage_type=str(damage),
            severity=str(severity),
            assignee_id=None,
            notes=notes,
        )
        created.append(repair)
        if det_id is not None:
            existing.add(det_id)
    return created
