from flask import Blueprint, render_template

from app.decorators import roles_required


protected_bp = Blueprint("protected", __name__)


@protected_bp.route("/users")
@roles_required("super_admin", "municipal_admin")
def users():
    return render_template(
        "placeholder.html",
        page_title="User Management",
        message="User management will be implemented in a later phase."
    )


@protected_bp.route("/municipalities")
@roles_required("super_admin")
def municipalities():
    return render_template(
        "placeholder.html",
        page_title="Municipalities",
        message="Municipality management will be implemented in a later phase."
    )


@protected_bp.route("/wards")
@roles_required("super_admin", "municipal_admin")
def wards():
    return render_template(
        "placeholder.html",
        page_title="Wards",
        message="Ward management will be implemented in a later phase."
    )


@protected_bp.route("/detections")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer"
)
def detections():
    return render_template(
        "placeholder.html",
        page_title="AI Detections",
        message="YOLO detection review will be implemented after Phase 4."
    )


@protected_bp.route("/repairs")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "contractor"
)
def repairs():
    return render_template(
        "placeholder.html",
        page_title="Repairs",
        message="Repair assignment and verification will be implemented later."
    )


@protected_bp.route("/map")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer"
)
def map_view():
    return render_template(
        "placeholder.html",
        page_title="GIS Map",
        message="Leaflet/PostGIS map integration will be implemented later."
    )


@protected_bp.route("/reports")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer"
)
def reports():
    return render_template(
        "placeholder.html",
        page_title="Reports",
        message="PDF, CSV, and JSON reports will be implemented later."
    )


@protected_bp.route("/settings")
@roles_required("super_admin")
def settings():
    return render_template(
        "placeholder.html",
        page_title="System Settings",
        message="System settings will be implemented later."
    )


@protected_bp.route("/profile")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "field_worker",
    "contractor",
    "viewer"
)
def profile():
    return render_template(
        "placeholder.html",
        page_title="Profile",
        message="Profile editing will be implemented later."
    )