from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.auth.constants import canonical_role, normalize_role
from app.services.dashboard_service import get_analytics_summary


dashboard_bp = Blueprint("dashboard", __name__)


ROLE_DASHBOARD_MAP = {
    # snake_case keys (live roles.role_name / dashboard_role_key)
    "super_admin": "dashboard/super_admin.html",
    "municipal_admin": "dashboard/admin.html",
    "administrator": "dashboard/admin.html",
    "field_officer": "dashboard/field_officer.html",
    "field_worker": "dashboard/field_worker.html",
    "contractor": "dashboard/contractor.html",
    "viewer": "dashboard/viewer.html",
    # phase-3 display / canonical names
    "super admin": "dashboard/super_admin.html",
    "municipal admin": "dashboard/admin.html",
    "field officer": "dashboard/field_officer.html",
    "field worker": "dashboard/field_worker.html",
}


def _resolve_dashboard_template(user) -> str | None:
    key = getattr(user, "dashboard_role_key", None)
    if key and key in ROLE_DASHBOARD_MAP:
        return ROLE_DASHBOARD_MAP[key]

    raw = (getattr(user, "role_name", None) or "").strip()
    candidates = [
        raw.lower().replace(" ", "_"),
        normalize_role(raw),
        normalize_role(canonical_role(raw) or ""),
    ]
    for candidate in candidates:
        if candidate and candidate in ROLE_DASHBOARD_MAP:
            return ROLE_DASHBOARD_MAP[candidate]
    return None


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """
    Show the dashboard that matches the signed-in role
    (snake_case DB names or phase-3 display names).
    """
    role_name = getattr(current_user, "dashboard_role_key", None) or (
        current_user.role_name or ""
    )
    dashboard_template = _resolve_dashboard_template(current_user)

    if dashboard_template is None:
        return render_template(
            "errors/403.html",
            unknown_role=role_name,
        ), 403

    municipality_id = None
    is_super = getattr(current_user, "is_super_admin", False)
    if callable(is_super):
        is_super = is_super()
    if not is_super:
        municipality_id = getattr(current_user, "municipality_id", None)

    analytics = None
    try:
        analytics = get_analytics_summary(municipality_id)
    except Exception:
        analytics = None

    return render_template(dashboard_template, analytics=analytics)


@dashboard_bp.route("/debug/current-role")
@login_required
def debug_current_role():
    is_super = current_user.is_super_admin
    if callable(is_super):
        is_super = is_super()

    return {
        "user_id": current_user.id,
        "pk": getattr(current_user, "pk", None),
        "username": current_user.username,
        "role_id": getattr(current_user, "role_id", None),
        "role_name_from_database": current_user.role_name,
        "dashboard_role_key": getattr(current_user, "dashboard_role_key", None),
        "is_super_admin": bool(is_super),
        "is_municipal_admin": current_user.is_admin(),
        "is_field_officer": current_user.is_field_officer(),
        "is_field_worker": current_user.is_field_worker(),
        "is_contractor": current_user.is_contractor(),
        "is_viewer": current_user.is_viewer(),
    }
