"""JSON API endpoints for map, analytics, and upload."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import csrf, db, limiter
from app.services.dashboard_service import get_analytics_summary
from app.services.geocoding_service import reverse_geocode
from app.services.inspection_service import (
    create_inspection_with_media,
    inspection_upload_payload,
)
from app.services.validation_service import validate_upload
from app.services.ward_service import list_wards_for_municipality
from app.utils.geo_utils import (
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


def _filter_map_payload(payload: dict) -> dict:
    flag = getattr(current_user, "is_contractor", False)
    is_contractor = flag() if callable(flag) else bool(flag)
    if not is_contractor or not isinstance(payload, dict):
        return payload
    from app.services.repair_service import list_repairs

    cid = getattr(current_user, "id", None) or getattr(current_user, "pk", None)
    allowed = {
        row.get("inspection_id")
        for row in list_repairs(assignee_id=cid)
        if row.get("inspection_id") is not None
    }
    features = [
        feature
        for feature in payload.get("features") or []
        if (feature.get("properties") or {}).get("inspection_id") in allowed
    ]
    filtered = dict(payload)
    filtered["features"] = features
    return filtered


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@api_bp.route("/inspections/<int:inspection_id>/before-image")
@login_required
def inspection_before_image(inspection_id: int):
    """Return the originally uploaded before image for an inspection."""
    from app.services.offline_store import offline_mode_enabled, offline_store
    from app.utils.media_urls import get_before_media, media_public_url

    inspection = None
    inspection = offline_store.get_inspection(inspection_id)
    if inspection is None:
        try:
            from app.models.inspection import Inspection

            inspection = db.session.get(Inspection, inspection_id)
        except Exception:
            inspection = None
    if inspection is None:
        return jsonify(
            {
                "url": None,
                "error": "No before image available for this location",
            }
        ), 404

    media = get_before_media(inspection)
    url = media_public_url(media)
    if not url:
        return jsonify(
            {
                "url": None,
                "inspection_id": inspection_id,
                "error": "No before image available for this location",
            }
        ), 404
    return jsonify(
        {
            "url": url,
            "inspection_id": inspection_id,
            "media_id": getattr(media, "id", None),
            "file_name": getattr(media, "file_name", None),
        }
    )


@api_bp.route("/geocode/reverse")
@login_required
def geocode_reverse():
    """Reverse geocode lat/lng → full street address (Nominatim)."""
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lng must be numbers."}), 400

    result = reverse_geocode(lat, lng)
    if not result:
        return jsonify(
            {
                "address": None,
                "display_name": None,
                "error": "No address found for these coordinates.",
            }
        ), 404

    from app.utils.location_format import decimal_to_dms, format_location_line

    location_line = format_location_line(result, lat=lat, lng=lng)
    return jsonify(
        {
            "address": result["address"],
            "display_name": result["display_name"],
            "road": result.get("road"),
            "street_name": result.get("street_name") or result.get("road"),
            "landmark": result.get("landmark"),
            "ward": result.get("ward"),
            "city": result.get("city"),
            "state": result.get("state"),
            "postcode": result.get("postcode"),
            "country": result.get("country"),
            "location_line": location_line,
            "latitude_dms": decimal_to_dms(lat, lat=True),
            "longitude_dms": decimal_to_dms(lng, lat=False),
            "lat": lat,
            "lng": lng,
        }
    )


@api_bp.route("/inspections/geojson")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
    "contractor",
)
def inspections_geojson():
    """GeoJSON FeatureCollection for Leaflet (detections with offline fallback)."""
    from app.services.map_service import map_detections_payload

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
        payload = map_detections_payload(
            municipality_id=_municipality_scope(),
            severity=severity or None,
            ward_id=ward_id,
            priority=priority or None,
            date_from=date_from,
            date_to=date_to,
        )
        return jsonify(_filter_map_payload(payload))
    except Exception:
        logger.exception("Failed to build inspections GeoJSON")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/map/detections")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
    "contractor",
)
def map_detections():
    """
    Phase 11 map detections GeoJSON.

    Includes lat/lng, severity, priority, damage type, thumbnail, timestamp.
    Offline-safe via in-memory store + Haversine-ready features.
    """
    from app.services.map_service import map_detections_payload

    severity = request.args.getlist("severity") or [
        s for s in (request.args.get("severity") or "").split(",") if s
    ]
    priority = request.args.getlist("priority") or [
        p for p in (request.args.get("priority") or "").split(",") if p
    ]
    ward_id = request.args.get("ward_id", type=int)
    municipality_id = request.args.get("municipality_id", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))
    scope = municipality_id if municipality_id is not None else _municipality_scope()

    try:
        payload = map_detections_payload(
            municipality_id=scope,
            severity=severity or None,
            ward_id=ward_id,
            priority=priority or None,
            date_from=date_from,
            date_to=date_to,
        )
        return jsonify(_filter_map_payload(payload))
    except Exception:
        logger.exception("Failed to build /api/map/detections")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/inspections/radius")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
    "contractor",
)
def inspections_radius():
    """Nearby detections/inspections (PostGIS or Haversine offline fallback)."""
    from app.services.map_service import map_radius_payload

    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
        # Accept radius meters or radius_km
        if request.args.get("radius_km") is not None:
            radius = float(request.args.get("radius_km")) * 1000.0
        else:
            radius = float(request.args.get("radius", "200"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat, lng, and radius must be numbers."}), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"error": "Invalid GPS coordinates."}), 400

    if radius <= 0 or radius > 50000:
        return jsonify({"error": "radius must be between 1 and 50000 meters."}), 400

    severity = request.args.getlist("severity") or None
    priority = request.args.getlist("priority") or None

    try:
        payload = map_radius_payload(
            latitude=lat,
            longitude=lng,
            radius_meters=radius,
            municipality_id=_municipality_scope(),
            severity=severity,
            priority=priority,
        )
        return jsonify(_filter_map_payload(payload))
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
    from app.services.offline_store import offline_mode_enabled

    try:
        if offline_mode_enabled():
            return jsonify(
                {
                    "type": "FeatureCollection",
                    "features": [],
                    "mode": "offline",
                    "note": "Ward boundaries require PostGIS; unavailable offline.",
                }
            )
        return jsonify(wards_boundaries_geojson(_municipality_scope()))
    except Exception:
        logger.exception("Ward boundaries GeoJSON failed")
        return jsonify({"type": "FeatureCollection", "features": [], "error": "query_failed"}), 500


@api_bp.route("/wards")
@login_required
def wards_list():
    from app.services.offline_store import offline_mode_enabled

    if offline_mode_enabled() or not current_app.config.get("DATABASE_CONFIGURED"):
        try:
            wards = list_wards_for_municipality(_municipality_scope())
        except Exception:
            wards = []
        return jsonify(
            {
                "wards": [
                    {
                        "ward_id": getattr(ward, "id", None),
                        "ward_name": getattr(ward, "name", None),
                        "ward_code": getattr(ward, "code", None),
                        "municipality_id": getattr(ward, "municipality_id", None),
                    }
                    for ward in wards
                ],
                "mode": "offline",
            }
        )
    try:
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
    except Exception:
        logger.exception("Wards list failed")
        return jsonify({"wards": [], "error": "query_failed"}), 500


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
@limiter.limit("20 per minute")
@login_required
@roles_required("field_worker", "municipal_admin", "super_admin")
def inspections_upload():
    """
    Multipart upload API.

    Fields: file|media_file, notes, location_name, location_latitude,
            location_longitude, manual_lat, manual_lng, gps_accuracy_meters
    """
    from app.services.audit_trail import record_audit_event
    from app.services.validation_service import validate_coordinates

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

    lat_raw = form.get("location_latitude")
    lng_raw = form.get("location_longitude")
    if lat_raw not in (None, "") or lng_raw not in (None, ""):
        coords = validate_coordinates(lat_raw, lng_raw)
        if not coords.get("valid"):
            return jsonify({"error": coords.get("error")}), 400

    if not form.get("location_name"):
        form["location_name"] = "Field upload"

    try:
        inspection = create_inspection_with_media(
            current_user=current_user,
            form_data=form,
            uploaded_file=uploaded_file,
        )
        payload = inspection_upload_payload(inspection)
        record_audit_event(
            "inspection.create",
            actor=str(getattr(current_user, "pk", getattr(current_user, "id", None))),
            resource_type="inspection",
            resource_id=payload.get("inspection_id") or getattr(inspection, "id", None),
            details={"source": "api_upload"},
        )
        return jsonify(payload), 201
    except ValueError as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"error": str(exc)}), 400
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception(
            "Upload API failed for user_id=%s",
            getattr(current_user, "pk", getattr(current_user, "id", None)),
        )
        return jsonify({"error": "Upload failed, please try again."}), 500


@api_bp.route("/inspections/<int:inspection_id>/analyze", methods=["POST"])
@csrf.exempt
@login_required
@roles_required(
    "field_worker",
    "municipal_admin",
    "super_admin",
    "field_officer",
)
def submit_analysis(inspection_id: int):
    """Enqueue Phase 6/7 background analysis; returns task_id immediately."""
    from app.services.offline_store import offline_mode_enabled, offline_store
    from app.services.task_service import submit_inspection_analysis

    if offline_mode_enabled():
        inspection = offline_store.get_inspection(inspection_id)
    else:
        from app.models.inspection import Inspection

        try:
            inspection = db.session.get(Inspection, inspection_id)
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            inspection = offline_store.get_inspection(inspection_id)

    if inspection is None:
        return jsonify({"error": "Inspection not found."}), 404

    media_id = request.json.get("media_id") if request.is_json else request.form.get("media_id")
    if media_id is not None:
        try:
            media_id = int(media_id)
        except (TypeError, ValueError):
            return jsonify({"error": "media_id must be an integer."}), 400

    try:
        task = submit_inspection_analysis(inspection_id, media_id)
        return jsonify(task), 202
    except Exception:
        logger.exception("Failed to submit analysis for inspection %s", inspection_id)
        return jsonify({"error": "Could not submit analysis task."}), 500


@api_bp.route("/tasks/<task_id>")
@login_required
def task_status(task_id: str):
    from app.services.task_service import get_task_status

    payload = get_task_status(task_id)
    if payload is None:
        return jsonify({"error": "Task not found."}), 404
    return jsonify(payload)


@api_bp.route("/tasks/<task_id>/result")
@login_required
def task_result(task_id: str):
    from app.services.task_service import get_task_result
    from app.workers.task_store import TASK_FAILED, TASK_SUCCEEDED

    payload = get_task_result(task_id)
    if payload is None:
        return jsonify({"error": "Task not found."}), 404

    status = payload.get("status")
    if status == TASK_SUCCEEDED:
        return jsonify(payload)
    if status == TASK_FAILED:
        return jsonify(payload), 200
    return jsonify(
        {
            "task_id": payload.get("task_id"),
            "status": status,
            "progress": payload.get("progress"),
            "message": "Task is still in progress.",
        }
    ), 202


@api_bp.route("/detections")
@limiter.limit("60 per minute")
@login_required
@roles_required(
    "field_worker",
    "field_officer",
    "municipal_admin",
    "super_admin",
    "viewer",
)
def detections_list():
    """
    List in-memory detections with Phase 10 severity fields.

    Offline/TESTING-safe — does not contact RDS.
    Query: inspection_id, manual_review=1
    """
    from app.services.detection_review_service import list_detections_for_review

    inspection_id = request.args.get("inspection_id", type=int)
    manual_only = request.args.get("manual_review", "").lower() in {"1", "true", "yes"}
    return jsonify(
        list_detections_for_review(
            inspection_id,
            manual_review_only=manual_only,
        )
    )


@api_bp.route("/detections/<int:detection_id>")
@login_required
@roles_required(
    "field_worker",
    "field_officer",
    "municipal_admin",
    "super_admin",
    "viewer",
)
def detections_detail(detection_id: int):
    from app.services.detection_review_service import get_detection_for_review

    payload = get_detection_for_review(detection_id)
    if payload is None:
        return jsonify({"error": "Detection not found."}), 404
    return jsonify(payload)


@api_bp.route("/detections/<int:detection_id>/review", methods=["POST"])
@login_required
@roles_required("field_officer", "municipal_admin", "super_admin")
def detections_review(detection_id: int):
    from app.services.review_service import review_detection

    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision") or request.form.get("decision")
    comment = payload.get("comment") or request.form.get("comment")
    try:
        updated = review_detection(
            detection_id,
            decision=decision or "",
            reviewer_name=getattr(current_user, "full_name", None),
            comment=comment,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if updated is None:
        return jsonify({"error": "Detection not found."}), 404
    return jsonify(updated)


@api_bp.route("/repairs/<int:repair_id>/mark-verified", methods=["POST"])
@login_required
@roles_required("super_admin", "municipal_admin", "field_officer")
def repairs_mark_verified(repair_id: int):
    from app.services.repair_service import get_repair
    from app.services.workflow_service import mark_repair_verified

    if get_repair(repair_id) is None:
        return jsonify({"error": "Repair not found."}), 404
    updated = mark_repair_verified(repair_id)
    return jsonify({"ok": True, "repair": updated})


@api_bp.route("/verification/compare", methods=["POST"])
@csrf.exempt
@limiter.limit("20 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
)
def verification_compare():
    """
    Compare before/after images (multipart or JSON paths).

    Fields:
      before|before_image, after|after_image (files)
      OR before_path, after_path (local paths — offline/testing)
      inspection_id, before_media_id, after_media_id (optional)
    """
    import tempfile
    from pathlib import Path

    from app.ai.before_after_verifier import VerificationError
    from app.services.comparison_service import run_verification

    before_file = request.files.get("before") or request.files.get("before_image")
    after_file = request.files.get("after") or request.files.get("after_image")
    before_path = request.form.get("before_path") or (
        request.json.get("before_path") if request.is_json else None
    )
    after_path = request.form.get("after_path") or (
        request.json.get("after_path") if request.is_json else None
    )

    inspection_id = request.form.get("inspection_id", type=int)
    if inspection_id is None and request.is_json:
        inspection_id = request.json.get("inspection_id")
        try:
            inspection_id = int(inspection_id) if inspection_id is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "inspection_id must be an integer."}), 400

    repair_id = request.form.get("repair_id", type=int)
    if repair_id is None and request.is_json:
        try:
            repair_id = int(request.json.get("repair_id")) if request.json.get("repair_id") is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "repair_id must be an integer."}), 400

    before_media_id = request.form.get("before_media_id", type=int)
    after_media_id = request.form.get("after_media_id", type=int)

    tmp_paths: list[Path] = []
    try:
        # Prefer linked inspection/repair files when IDs are provided (correct road).
        if repair_id or inspection_id:
            from app.services.repair_service import get_repair
            from app.services.workflow_service import get_inspection_by_id
            from app.utils.media_urls import get_before_media, resolve_original_path

            repair = get_repair(repair_id) if repair_id else None
            if repair and not inspection_id:
                inspection_id = repair.get("inspection_id")
            if repair and repair.get("evidence_path") and Path(repair["evidence_path"]).is_file():
                after_path = Path(repair["evidence_path"])
            if inspection_id:
                inspection = get_inspection_by_id(inspection_id)
                media = get_before_media(inspection) if inspection else None
                local_before = resolve_original_path(media)
                if local_before is not None:
                    before_path = local_before
                    before_media_id = before_media_id or getattr(media, "id", None)

        if before_file and after_file and not (before_path and after_path):
            tmp = Path(tempfile.mkdtemp(prefix="verify_"))
            before_path = tmp / "before.jpg"
            after_path = tmp / "after.jpg"
            before_file.save(before_path)
            after_file.save(after_path)
            tmp_paths.extend([before_path, after_path])
        elif before_file and after_path and not before_path:
            tmp = Path(tempfile.mkdtemp(prefix="verify_"))
            before_path = tmp / "before.jpg"
            before_file.save(before_path)
            tmp_paths.append(before_path)
            after_path = Path(after_path)
        elif after_file and before_path and not after_path:
            tmp = Path(tempfile.mkdtemp(prefix="verify_"))
            after_path = tmp / "after.jpg"
            after_file.save(after_path)
            tmp_paths.append(after_path)
            before_path = Path(before_path)
        elif before_path and after_path:
            before_path = Path(before_path)
            after_path = Path(after_path)
        elif before_file and after_file:
            tmp = Path(tempfile.mkdtemp(prefix="verify_"))
            before_path = tmp / "before.jpg"
            after_path = tmp / "after.jpg"
            before_file.save(before_path)
            after_file.save(after_path)
            tmp_paths.extend([before_path, after_path])
        else:
            return jsonify(
                {
                    "error": (
                        "Provide before/after images, or open Verify with an "
                        "inspection_id / repair_id so the before image loads automatically."
                    )
                }
            ), 400

        if not Path(before_path).is_file() or not Path(after_path).is_file():
            return jsonify({"error": "Before/after image files were not found."}), 400

        record = run_verification(
            before_path,
            after_path,
            inspection_id=inspection_id,
            before_media_id=before_media_id,
            after_media_id=after_media_id,
        )
        from app.services.audit_trail import record_audit_event

        record_audit_event(
            "verification.compare",
            actor=str(getattr(current_user, "pk", getattr(current_user, "id", None))),
            resource_type="comparison",
            resource_id=record.get("comparison_id"),
            details={
                "inspection_id": inspection_id,
                "repair_id": repair_id,
                "ssim": record.get("ssim"),
                "status": record.get("status"),
            },
        )

        # Link SSIM result back to the repair job when verified (or partial with repair_id).
        status_text = str(record.get("status") or "")
        if repair_id and (
            status_text == "Repair Verified"
            or "Partial" in status_text
        ):
            from app.services.workflow_service import mark_repair_verified

            # Auto-verify only on clear "Repair Verified"; partial needs manual confirm.
            if status_text == "Repair Verified":
                mark_repair_verified(
                    repair_id,
                    comparison_id=record.get("comparison_id"),
                )
                record["repair_status"] = "verified"
            record["repair_id"] = repair_id

        return jsonify(record), 201
    except VerificationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Verification compare failed")
        return jsonify({"error": "Verification failed."}), 500


@api_bp.route("/verification/<comparison_id>")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
)
def verification_detail(comparison_id: str):
    from app.services.comparison_service import get_comparison

    payload = get_comparison(comparison_id)
    if payload is None:
        return jsonify({"error": "Verification comparison not found."}), 404
    return jsonify(payload)


@api_bp.route("/verification")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
)
def verification_list():
    from app.services.comparison_service import list_comparisons

    inspection_id = request.args.get("inspection_id", type=int)
    rows = list_comparisons(inspection_id=inspection_id)
    return jsonify({"count": len(rows), "comparisons": rows})


def _send_report_download(inspection_id: int, fmt: str):
    from flask import send_file

    from app.services.audit_trail import record_audit_event
    from app.services.report_service import (
        compile_inspection_report,
        render_report_bytes,
    )
    from app.services.validation_service import validate_report_format

    fmt_check = validate_report_format(fmt)
    if not fmt_check.get("valid"):
        return jsonify({"error": fmt_check.get("error")}), 400
    fmt = fmt_check["format"]

    try:
        report = compile_inspection_report(inspection_id)
    except LookupError:
        return jsonify({"error": "Inspection not found."}), 404
    except Exception:
        logger.exception("Report compile failed for %s", inspection_id)
        return jsonify({"error": "Could not compile report."}), 500

    try:
        content, mime, ext = render_report_bytes(report, fmt)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Report render failed format=%s id=%s", fmt, inspection_id)
        return jsonify({"error": "Could not render report."}), 500

    code = (report.get("header") or {}).get("inspection_code") or f"INS-{inspection_id}"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(code))
    filename = f"{safe}_report{ext}"
    record_audit_event(
        "report.download",
        actor=str(getattr(current_user, "pk", getattr(current_user, "id", None))),
        resource_type="inspection",
        resource_id=inspection_id,
        details={"format": fmt, "filename": filename},
    )
    return send_file(
        io.BytesIO(content),
        mimetype=mime,
        as_attachment=True,
        download_name=filename,
    )


@api_bp.route("/reports/<int:inspection_id>/json")
@limiter.limit("30 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_json(inspection_id: int):
    return _send_report_download(inspection_id, "json")


@api_bp.route("/reports/<int:inspection_id>/csv")
@limiter.limit("30 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_csv(inspection_id: int):
    return _send_report_download(inspection_id, "csv")


@api_bp.route("/reports/<int:inspection_id>/excel")
@limiter.limit("30 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_excel(inspection_id: int):
    return _send_report_download(inspection_id, "excel")


@api_bp.route("/reports/<int:inspection_id>/pdf")
@limiter.limit("30 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_pdf(inspection_id: int):
    return _send_report_download(inspection_id, "pdf")


@api_bp.route("/reports/export", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("20 per minute")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_export():
    """
    Export or enqueue report.

    Query/body: inspection_id, format=pdf|csv|excel|json,
    async=1 for background job.
    """
    from app.services.validation_service import (
        validate_positive_int,
        validate_report_format,
    )

    if request.method == "POST" and request.is_json:
        inspection_id = request.json.get("inspection_id")
        fmt = request.json.get("format", "pdf")
        async_mode = bool(request.json.get("async"))
    else:
        inspection_id = request.values.get("inspection_id", type=int)
        fmt = request.values.get("format", "pdf")
        async_mode = request.values.get("async", "").lower() in {
            "1",
            "true",
            "yes",
        }

    id_check = validate_positive_int(inspection_id, "inspection_id")
    if not id_check.get("valid"):
        return jsonify({"error": id_check.get("error")}), 400
    inspection_id = id_check["value"]

    fmt_check = validate_report_format(fmt)
    if not fmt_check.get("valid"):
        return jsonify({"error": fmt_check.get("error")}), 400
    fmt = fmt_check["format"]

    if async_mode:
        from app.services.audit_trail import record_audit_event
        from app.services.report_service import create_background_export_job

        try:
            job = create_background_export_job(inspection_id, fmt)
            record_audit_event(
                "report.export_async",
                actor=str(
                    getattr(current_user, "pk", getattr(current_user, "id", None))
                ),
                resource_type="inspection",
                resource_id=inspection_id,
                details={"format": fmt, "job_id": job.get("job_id")},
            )
            return jsonify(job), 202
        except LookupError:
            return jsonify({"error": "Inspection not found."}), 404
        except Exception:
            logger.exception("Async report export failed")
            return jsonify({"error": "Could not enqueue report job."}), 500

    return _send_report_download(inspection_id, fmt)


@api_bp.route("/reports/jobs/<job_id>")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_job_status(job_id: str):
    from app.services.report_service import get_report_job

    job = get_report_job(job_id)
    if job is None:
        return jsonify({"error": "Report job not found."}), 404
    return jsonify(job)


@api_bp.route("/reports/<int:inspection_id>/summary")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
)
def report_summary(inspection_id: int):
    from app.services.report_service import compile_inspection_report

    try:
        report = compile_inspection_report(inspection_id)
    except LookupError:
        return jsonify({"error": "Inspection not found."}), 404
    return jsonify(report)

