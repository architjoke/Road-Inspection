import json
from decimal import Decimal

from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required

from app.auth.constants import canonical_role, normalize_role
from app.services.dashboard_service import get_analytics_summary


def _is_super_user(user) -> bool:
    flag = getattr(user, "is_super_admin", False)
    if callable(flag):
        flag = flag()
    return bool(flag)


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _empty_analytics() -> dict:
    return {
        "cards": {
            "total_inspections": 0,
            "inspections_last_30_days": 0,
            "total_detections": 0,
            "avg_severity_score": 0,
            "repairs_completed_this_month": 0,
            "processing": 0,
            "completed": 0,
        },
        "severity_bar": {"labels": ["SEVERE", "MODERATE", "MINOR"], "values": [0, 0, 0]},
        "damage_pie": {
            "labels": ["Pothole", "Longitudinal Crack", "Lateral Crack", "Alligator Crack"],
            "values": [0, 0, 0, 0],
        },
        "inspections_per_week": {"labels": [], "values": []},
        "ward_breakdown": [],
    }


dashboard_bp = Blueprint("dashboard", __name__)


ROLE_DASHBOARD_MAP = {
    "super_admin": "dashboard/super_admin.html",
    "municipal_admin": "dashboard/admin.html",
    "administrator": "dashboard/admin.html",
    "field_officer": "dashboard/field_officer.html",
    "field_worker": "dashboard/field_worker.html",
    "contractor": "dashboard/contractor.html",
    "viewer": "dashboard/viewer.html",
    "super admin": "dashboard/super_admin.html",
    "municipal admin": "dashboard/admin.html",
    "field officer": "dashboard/field_officer.html",
    "field worker": "dashboard/field_worker.html",
}


def _resolve_dashboard_template(user) -> str:
    key = getattr(user, "dashboard_role_key", None)
    if key and key in ROLE_DASHBOARD_MAP:
        return ROLE_DASHBOARD_MAP[key]

    raw = (getattr(user, "role_name", None) or "").strip()
    candidates = [
        raw.lower().replace(" ", "_"),
        normalize_role(raw),
        normalize_role(canonical_role(raw) or ""),
        (canonical_role(raw) or "").lower().replace(" ", "_"),
    ]
    for candidate in candidates:
        if candidate and candidate in ROLE_DASHBOARD_MAP:
            return ROLE_DASHBOARD_MAP[candidate]
    return "dashboard/viewer.html"


@dashboard_bp.route("/dashboard")
@login_required
def index():
    dashboard_template = _resolve_dashboard_template(current_user)

    municipality_id = None
    if not _is_super_user(current_user):
        municipality_id = getattr(current_user, "municipality_id", None)

    analytics = _empty_analytics()
    try:
        loaded = get_analytics_summary(municipality_id)
        if isinstance(loaded, dict):
            analytics.update(_json_safe(loaded))
            if isinstance(loaded.get("cards"), dict):
                analytics["cards"] = {
                    **_empty_analytics()["cards"],
                    **_json_safe(loaded["cards"]),
                }
        analytics = _json_safe(analytics)
        json.dumps(analytics)
    except Exception:
        current_app.logger.exception("Dashboard analytics failed; using empty cards.")

    from app.services.repair_service import list_repairs, repair_stats

    role_key = getattr(current_user, "dashboard_role_key", "") or ""
    assignee_id = None
    if role_key == "contractor":
        assignee_id = getattr(current_user, "id", None) or getattr(current_user, "pk", None)

    assigned_repairs = []
    stats = {"assigned": 0, "in_progress": 0, "completed": 0, "evidence_due": 0, "total": 0}
    try:
        assigned_repairs = list_repairs(assignee_id=assignee_id) if role_key == "contractor" else []
        stats = repair_stats(assignee_id=assignee_id if role_key == "contractor" else None)
    except Exception:
        current_app.logger.exception("Dashboard repair stats failed.")

    attention_repairs = [
        job
        for job in assigned_repairs
        if (job.get("status") or "") in {"open", "assigned", "in_progress", "rejected"}
    ]

    try:
        return render_template(
            dashboard_template,
            analytics=analytics,
            repairs=assigned_repairs[:15],
            attention_repairs=attention_repairs[:8],
            repair_stats=stats,
        )
    except Exception:
        current_app.logger.exception("Dashboard template %s failed", dashboard_template)
        return render_template(
            "dashboard/viewer.html",
            analytics=_empty_analytics(),
            repairs=[],
            attention_repairs=[],
            repair_stats=stats,
        )


@dashboard_bp.route("/debug/current-role")
@login_required
def debug_current_role():
    is_super = current_user.is_super_admin
    if callable(is_super):
        is_super = is_super()

    def _call(name):
        fn = getattr(current_user, name, None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        return bool(fn)

    return {
        "user_id": current_user.id,
        "pk": getattr(current_user, "pk", None),
        "username": current_user.username,
        "role_id": getattr(current_user, "role_id", None),
        "role_name_from_database": current_user.role_name,
        "dashboard_role_key": getattr(current_user, "dashboard_role_key", None),
        "is_super_admin": bool(is_super),
        "is_municipal_admin": _call("is_admin"),
        "is_field_officer": _call("is_field_officer"),
        "is_field_worker": _call("is_field_worker"),
        "is_contractor": _call("is_contractor"),
        "is_viewer": _call("is_viewer"),
    }
