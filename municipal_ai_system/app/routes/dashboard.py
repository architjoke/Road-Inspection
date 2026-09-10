from flask import Blueprint, render_template
from flask_login import current_user, login_required


dashboard_bp = Blueprint("dashboard", __name__)


ROLE_DASHBOARD_MAP = {
    "super_admin": "dashboard/super_admin.html",
    "municipal_admin": "dashboard/admin.html",
    "field_officer": "dashboard/field_officer.html",
    "field_worker": "dashboard/field_worker.html",
    "contractor": "dashboard/contractor.html",
    "viewer": "dashboard/viewer.html",
}


@dashboard_bp.route("/dashboard")
@login_required
def index():
    """
    Show the dashboard that directly matches the role_name
    returned from the existing PostgreSQL roles table.
    """
    role_name = getattr(current_user, "dashboard_role_key", None) or (
        current_user.role_name or ""
    )
    dashboard_template = ROLE_DASHBOARD_MAP.get(role_name)

    if dashboard_template is None:
        return render_template(
            "errors/403.html",
            unknown_role=role_name
        ), 403

    return render_template(dashboard_template)


@dashboard_bp.route("/debug/current-role")
@login_required
def debug_current_role():
    """
    Temporary role-mapping diagnostic endpoint.

    While logged in, open:
    http://127.0.0.1:5000/debug/current-role

    Remove this route after Phase 3 role testing is complete.
    """
    is_super = current_user.is_super_admin
    if callable(is_super):
        is_super = is_super()

    return {
        "user_id": current_user.id,
        "username": current_user.username,
        "role_id": getattr(current_user, "role_id", None),
        "role_name_from_database": current_user.role_name,
        "is_super_admin": bool(is_super),
        "is_municipal_admin": current_user.is_admin(),
        "is_field_officer": current_user.is_field_officer(),
        "is_field_worker": current_user.is_field_worker(),
        "is_contractor": current_user.is_contractor(),
        "is_viewer": current_user.is_viewer()
    }