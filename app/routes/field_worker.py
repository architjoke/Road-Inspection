"""Field Worker live camera capture — GPS + ImageKit inspection create."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import db, limiter
from app.services.audit_trail import record_audit_event
from app.services.geocoding_service import reverse_geocode
from app.services.inspection_service import create_inspection_with_media, inspection_upload_payload
from app.services.validation_service import validate_coordinates, validate_upload
from app.services.ward_service import assign_ward_from_gps
from app.utils.location_format import format_capture_location

logger = logging.getLogger(__name__)

field_worker_bp = Blueprint("field_worker", __name__, url_prefix="/field_worker")

CAPTURE_MAX_BYTES = 10 * 1024 * 1024


@field_worker_bp.route("/dashboard")
@login_required
@roles_required("field_worker")
def dashboard():
    return redirect(url_for("dashboard.index"))


@field_worker_bp.route("/api/capture", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
@roles_required("field_worker")
def api_capture():
    """Create an inspection from a live camera JPEG plus browser GPS."""
    uploaded = request.files.get("image") or request.files.get("file")
    validation = validate_upload(uploaded)
    if not validation.get("valid"):
        return jsonify({"success": False, "error": validation.get("error", "Invalid image.")}), 400

    if validation.get("media_type") != "image":
        return jsonify({"success": False, "error": "Camera capture must be a JPEG or PNG image."}), 400

    size = validation.get("size_bytes") or 0
    if size > CAPTURE_MAX_BYTES:
        return jsonify(
            {
                "success": False,
                "error": "Image is larger than 10 MB. Retake the photo and try again.",
            }
        ), 400

    lat_raw = request.form.get("latitude") or request.form.get("location_latitude")
    lng_raw = request.form.get("longitude") or request.form.get("location_longitude")
    if lat_raw in (None, "") or lng_raw in (None, ""):
        return jsonify(
            {
                "success": False,
                "error": "GPS coordinates are required. Enable location and capture again.",
            }
        ), 400

    coords = validate_coordinates(lat_raw, lng_raw)
    if not coords.get("valid"):
        return jsonify({"success": False, "error": coords.get("error")}), 400

    latitude = coords["latitude"]
    longitude = coords["longitude"]
    accuracy = request.form.get("accuracy") or request.form.get("gps_accuracy_meters")
    timestamp = request.form.get("timestamp")

    geocoded = None
    try:
        geocoded = reverse_geocode(latitude, longitude)
    except Exception:
        logger.exception("Reverse geocoding failed for capture lat=%s lng=%s", latitude, longitude)
        geocoded = None

    location_name = format_capture_location(geocoded)
    if not geocoded:
        location_name = "Unknown Location"
        logger.warning(
            "Capture geocoding unavailable; storing Unknown Location lat=%s lng=%s",
            latitude,
            longitude,
        )

    ward_info = None
    try:
        ward_info = assign_ward_from_gps(
            latitude,
            longitude,
            municipality_id=getattr(current_user, "municipality_id", None),
        )
    except Exception:
        logger.exception("Ward assignment failed for capture lat=%s lng=%s", latitude, longitude)

    form = {
        "location_name": location_name,
        "location_latitude": str(latitude),
        "location_longitude": str(longitude),
        "gps_accuracy_meters": str(accuracy) if accuracy not in (None, "") else "",
        "gps_manual_override": "",
        "capture_mode": "1",
        "notes": "Live camera capture",
        "client_timestamp": timestamp or "",
    }

    try:
        inspection = create_inspection_with_media(
            current_user=current_user,
            form_data=form,
            uploaded_file=uploaded,
        )
    except ValueError as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception(
            "Field worker capture failed user_id=%s",
            getattr(current_user, "pk", getattr(current_user, "id", None)),
        )
        return jsonify(
            {
                "success": False,
                "error": "Could not save the inspection. Check your connection and try again.",
            }
        ), 500

    payload = inspection_upload_payload(inspection)
    record_audit_event(
        "inspection.camera_capture",
        actor=str(getattr(current_user, "pk", getattr(current_user, "id", None))),
        resource_type="inspection",
        resource_id=payload.get("inspection_id"),
        details={
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "location_name": location_name,
            "ward_id": (ward_info or {}).get("ward_id") or payload.get("ward_id"),
        },
    )

    code = payload.get("inspection_code") or payload.get("inspection_id")
    return (
        jsonify(
            {
                "success": True,
                "inspection_id": payload.get("inspection_id"),
                "inspection_code": payload.get("inspection_code"),
                "message": (
                    f"Capture successful! Inspection {code} created at {location_name}"
                ),
                "location_name": location_name,
                "ward_name": payload.get("ward_assigned") or (ward_info or {}).get("ward_name"),
                "ward_id": payload.get("ward_id") or (ward_info or {}).get("ward_id"),
                "coordinates": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": float(accuracy) if accuracy not in (None, "") else None,
                },
                "image_url": payload.get("image_url"),
                "analysis_task": payload.get("analysis_task"),
            }
        ),
        201,
    )
