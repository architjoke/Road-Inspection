"""JSON API endpoints for map, analytics, and upload."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import csrf, db
from app.services.dashboard_service import get_analytics_summary
from app.services.inspection_service import (
    create_inspection_with_media,
    inspection_upload_payload,
)
from app.services.validation_service import validate_upload
from app.services.ward_service import list_wards_for_municipality
from app.utils.geo_utils import (
    detections_as_geojson,
    radius_search,
    wards_boundaries_geojson,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _municipality_scope() -> int | None:
    if getattr(current_user, "is_super_admin", False):
        flag = current_user.is_super_admin
        if callable(flag):
            flag = flag()
        if flag:
            return None
    return getattr(current_user, "municipality_id", None)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@api_bp.route("/inspections/geojson")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def inspections_geojson():
    """GeoJSON FeatureCollection for Leaflet (detections with inspection fallback)."""
    severity = request.args.getlist("severity") or [
        s for s in (request.args.get("severity") or "").split(",") if s
    ]
    priority = request.args.getlist("priority") or [
        p for p in (request.args.get("priority") or "").split(",") if p
    ]
    ward_id = request.args.get("ward_id", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))

    try:
        payload = detections_as_geojson(
            municipality_id=_municipality_scope(),
            severity=severity or None,
            ward_id=ward_id,
            priority=priority or None,
            date_from=date_from,
            date_to=date_to,
        )
        return jsonify(payload)
    except Exception:
        logger.exception("Failed to build inspections GeoJSON")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/inspections/radius")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def inspections_radius():
    """Nearby inspections via PostGIS ST_DWithin (meters)."""
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
        radius = float(request.args.get("radius", "200"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat, lng, and radius must be numbers."}), 400

    if radius <= 0 or radius > 50000:
        return jsonify({"error": "radius must be between 1 and 50000 meters."}), 400

    try:
        payload = radius_search(
            latitude=lat,
            longitude=lng,
            radius_meters=radius,
            municipality_id=_municipality_scope(),
        )
        return jsonify(payload)
    except Exception:
        logger.exception("Radius search failed")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/wards/boundaries")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def wards_boundaries():
    try:
        return jsonify(wards_boundaries_geojson(_municipality_scope()))
    except Exception:
        logger.exception("Ward boundaries GeoJSON failed")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/wards")
@login_required
def wards_list():
    wards = list_wards_for_municipality(_municipality_scope())
    return jsonify(
        {
            "wards": [
                {
                    "ward_id": ward.id,
                    "ward_name": ward.name,
                    "ward_code": ward.code,
                    "municipality_id": ward.municipality_id,
                }
                for ward in wards
            ]
        }
    )


@api_bp.route("/analytics/summary")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
)
def analytics_summary():
    try:
        return jsonify(get_analytics_summary(_municipality_scope()))
    except Exception:
        logger.exception("Analytics summary failed")
        return jsonify({"error": "Unable to load analytics."}), 500


@api_bp.route("/inspections/upload", methods=["POST"])
@csrf.exempt
@login_required
@roles_required("field_worker", "municipal_admin", "super_admin")
def inspections_upload():
    """
    Multipart upload API.

    Fields: file|media_file, notes, location_name, location_latitude,
            location_longitude, manual_lat, manual_lng, gps_accuracy_meters
    """
    uploaded_file = request.files.get("file") or request.files.get("media_file")
    validation = validate_upload(uploaded_file)

    if not validation.get("valid"):
        return jsonify({"error": validation.get("error", "Invalid upload.")}), 400

    form = request.form.to_dict()

    # Support Phase-5 manual GPS field names as aliases.
    if form.get("manual_lat") and not form.get("location_latitude"):
        form["location_latitude"] = form["manual_lat"]
        form["gps_manual_override"] = "1"
    if form.get("manual_lng") and not form.get("location_longitude"):
        form["location_longitude"] = form["manual_lng"]
        form["gps_manual_override"] = "1"

    if not form.get("location_name"):
        form["location_name"] = "Field upload"

    try:
        inspection = create_inspection_with_media(
            current_user=current_user,
            form_data=form,
            uploaded_file=uploaded_file,
        )
        payload = inspection_upload_payload(inspection)
        return jsonify(payload), 201
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        logger.exception(
            "Upload API failed for user_id=%s",
            getattr(current_user, "pk", getattr(current_user, "id", None)),
        )
        return jsonify({"error": "Upload failed, please try again."}), 500
