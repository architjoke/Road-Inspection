"""Create inspections with media, GPS, and optional ward assignment."""

from __future__ import annotations

import logging
from datetime import datetime

from geoalchemy2.elements import WKTElement

from app.extensions import db
from app.models.inspection import Inspection
from app.models.inspection_media import InspectionMedia
from app.services.gps_service import resolve_inspection_gps_details
from app.services.geocoding_service import reverse_geocode
from app.utils.location_format import decimal_to_dms, format_location_line
from app.services.media_service import save_phase4_temp_media
from app.services.offline_store import offline_mode_enabled, offline_store
from app.services.ward_service import find_ward_for_point
from app.utils.file_validation import get_media_type
from app.utils.identifiers import generate_inspection_code
from app.utils.status_constants import MEDIA_PENDING, PENDING_ANALYSIS

logger = logging.getLogger(__name__)


def _offline_inspection_code() -> str:
    return f"INS-OFF-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def _create_inspection_offline(
    current_user,
    *,
    location_name: str,
    notes_text: str | None,
    weather_conditions: str | None,
    lighting_conditions: str | None,
    latitude,
    longitude,
    accuracy,
    gps_source: str,
    needs_review: bool,
    street_address: str | None,
    geocoded: dict | None,
    uploaded_file,
    gps_review_reasons: list[str] | None = None,
):
    gps_review_reasons = gps_review_reasons or []
    municipality_id = current_user.municipality_id
    inspection_code = _offline_inspection_code()
    tags = [
        inspection_code,
        "ward_unassigned",
        f"user_{getattr(current_user, 'pk', current_user.id)}",
    ]
    saved_media = save_phase4_temp_media(
        uploaded_file=uploaded_file,
        inspection_code=inspection_code,
        tags=tags,
    )

    inspection = offline_store.create_inspection(
        inspection_code=inspection_code,
        municipality_id=municipality_id,
        user_id=getattr(current_user, "pk", current_user.id),
        ward_id=None,
        inspection_date=datetime.now(),
        location_name=location_name,
        location_latitude=latitude,
        location_longitude=longitude,
        status=PENDING_ANALYSIS,
        inspection_type="routine",
        notes=notes_text,
        weather_conditions=weather_conditions or None,
        lighting_conditions=lighting_conditions or None,
        total_detections=0,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    media = offline_store.add_media(
        inspection.id,
        media_type=saved_media["media_type"],
        file_path=saved_media.get("local_path") or saved_media["file_path"],
        file_name=saved_media["file_name"],
        file_size_bytes=saved_media["file_size_bytes"],
        mime_type=saved_media["mime_type"],
        resolution_width=saved_media["resolution_width"],
        resolution_height=saved_media["resolution_height"],
        duration_seconds=saved_media["duration_seconds"],
        captured_at=saved_media["captured_at"],
        gps_latitude=latitude,
        gps_longitude=longitude,
        gps_accuracy_meters=accuracy,
        exif_data={
            "gps_source": gps_source,
            "needs_review": needs_review,
            "gps_review_reasons": gps_review_reasons,
            "imagekit_file_id": saved_media.get("image_id"),
            "street_address": street_address,
            "street_name": (geocoded or {}).get("street_name") or (geocoded or {}).get("road"),
            "landmark": (geocoded or {}).get("landmark"),
            "ward": (geocoded or {}).get("ward"),
            "city": (geocoded or {}).get("city"),
            "latitude_dms": decimal_to_dms(float(latitude), lat=True),
            "longitude_dms": decimal_to_dms(float(longitude), lat=False),
            "geocode_display_name": (geocoded or {}).get("display_name"),
            "local_path": saved_media.get("local_path"),
            "gps_accuracy_meters": float(accuracy) if accuracy is not None else None,
            "location_details": {
                "street_name": (geocoded or {}).get("street_name") or (geocoded or {}).get("road"),
                "landmark": (geocoded or {}).get("landmark"),
                "ward": (geocoded or {}).get("ward"),
                "city": (geocoded or {}).get("city"),
                "state": (geocoded or {}).get("state"),
                "country": (geocoded or {}).get("country"),
            },
            "offline": True,
        },
        storage_bucket=saved_media["storage_bucket"],
        cdn_url=saved_media["cdn_url"],
        thumbnail_path=saved_media["thumbnail_path"],
        processing_status=MEDIA_PENDING,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    # Refresh with media_items
    inspection = offline_store.get_inspection(inspection.id)

    analysis_task = None
    try:
        from app.services.task_service import submit_inspection_analysis

        analysis_task = submit_inspection_analysis(
            inspection_id=inspection.id,
            media_id=media.id,
        )
    except Exception:
        logger.exception(
            "Failed to enqueue analysis for offline inspection %s",
            inspection.id,
        )

    inspection._analysis_task = analysis_task
    return inspection


def create_inspection_with_media(
    current_user,
    form_data,
    uploaded_file,
) -> Inspection:
    municipality_id = current_user.municipality_id

    if municipality_id is None:
        raise ValueError(
            "Your account is not assigned to a municipality. "
            "Please contact the Municipal Admin."
        )

    location_name = (form_data.get("location_name") or "").strip()
    notes = (form_data.get("notes") or "").strip()
    weather_conditions = (form_data.get("weather_conditions") or "").strip()
    lighting_conditions = (form_data.get("lighting_conditions") or "").strip()

    media_type = get_media_type(uploaded_file.filename or "")

    gps = resolve_inspection_gps_details(
        form_data=form_data,
        uploaded_file=uploaded_file,
        media_type=media_type,
    )
    latitude, longitude = gps["latitude"], gps["longitude"]
    accuracy, gps_source = gps["accuracy"], gps["source"]
    needs_review = gps["needs_review"]
    gps_review_reasons = gps["review_reasons"]

    # Auto street address from GPS (Nominatim). Skipped offline (no internet).
    geocoded = None
    street_address = None
    if not offline_mode_enabled():
        geocoded = reverse_geocode(float(latitude), float(longitude))
        street_address = (geocoded or {}).get("address") if geocoded else None

    placeholder_names = {
        "",
        "field upload",
        "road inspection",
        "location",
        "unknown",
    }
    geo_parts = geocoded or {}
    formatted = format_location_line(
        {
            "street_name": geo_parts.get("street_name") or geo_parts.get("road"),
            "landmark": geo_parts.get("landmark"),
            "ward": geo_parts.get("ward"),
            "city": geo_parts.get("city"),
            "address": street_address,
        },
        lat=latitude,
        lng=longitude,
    )
    capture_mode = str(form_data.get("capture_mode") or "").strip() in {"1", "true", "yes"}
    if capture_mode:
        from app.utils.location_format import format_capture_location

        formatted = format_capture_location(geo_parts)
        if formatted == "Unknown Location" and not geocoded:
            location_name = location_name or "Unknown Location"
        elif not location_name or location_name.lower() in placeholder_names:
            location_name = formatted
    elif not location_name or location_name.lower() in placeholder_names:
        location_name = formatted
    elif street_address and street_address.lower() not in location_name.lower():
        if len(location_name) < 80:
            location_name = f"{location_name} — {street_address}"
    if len(location_name) > 255:
        location_name = location_name[:252] + "…"

    if not location_name:
        raise ValueError("Road or location name is required.")

    if needs_review:
        logger.warning(
            "GPS flagged for review for user_id=%s (lat=%s, lng=%s, source=%s): %s",
            getattr(current_user, "pk", current_user.id),
            latitude,
            longitude,
            gps_source,
            "; ".join(gps_review_reasons),
        )

    note_parts = []
    if notes:
        note_parts.append(notes)
    if gps_source == "exif":
        note_parts.append("[GPS from photo EXIF]")
    elif gps_source == "video_metadata":
        note_parts.append("[GPS from video metadata]")
    elif gps_source == "manual":
        note_parts.append("[GPS pinned manually]")
    if needs_review:
        note_parts.append(f"[GPS review: {'; '.join(gps_review_reasons)}]")
    if street_address:
        note_parts.append(f"[Address: {street_address}]")

    notes_text = " ".join(note_parts) or None

    if offline_mode_enabled():
        return _create_inspection_offline(
            current_user,
            location_name=location_name,
            notes_text=notes_text,
            weather_conditions=weather_conditions or None,
            lighting_conditions=lighting_conditions or None,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            gps_source=gps_source,
            needs_review=needs_review,
            street_address=street_address,
            geocoded=geocoded,
            uploaded_file=uploaded_file,
            gps_review_reasons=gps_review_reasons,
        )

    try:
        inspection_code = generate_inspection_code()

        ward = find_ward_for_point(
            float(latitude),
            float(longitude),
            municipality_id=municipality_id,
        )
        ward_id = ward.id if ward else None

        inspection = Inspection(
            inspection_code=inspection_code,
            municipality_id=municipality_id,
            ward_id=ward_id,
            user_id=getattr(current_user, "pk", current_user.id),
            inspection_date=datetime.now(),
            location_name=location_name,
            location_latitude=latitude,
            location_longitude=longitude,
            location_geometry=WKTElement(
                f"POINT({longitude} {latitude})",
                srid=4326,
            ),
            status=PENDING_ANALYSIS,
            inspection_type="routine",
            notes=notes_text,
            weather_conditions=weather_conditions or None,
            lighting_conditions=lighting_conditions or None,
            total_detections=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        db.session.add(inspection)
        db.session.flush()

        tags = [
            inspection_code,
            f"ward_{ward_id}" if ward_id else "ward_unassigned",
            f"user_{getattr(current_user, 'pk', current_user.id)}",
        ]

        saved_media = save_phase4_temp_media(
            uploaded_file=uploaded_file,
            inspection_code=inspection_code,
            tags=tags,
        )

        media = InspectionMedia(
            inspection_id=inspection.id,
            media_type=saved_media["media_type"],
            file_path=saved_media["file_path"],
            file_name=saved_media["file_name"],
            file_size_bytes=saved_media["file_size_bytes"],
            mime_type=saved_media["mime_type"],
            resolution_width=saved_media["resolution_width"],
            resolution_height=saved_media["resolution_height"],
            duration_seconds=saved_media["duration_seconds"],
            captured_at=saved_media["captured_at"],
            gps_latitude=latitude,
            gps_longitude=longitude,
            gps_accuracy_meters=accuracy,
            exif_data={
                "gps_source": gps_source,
                "needs_review": needs_review,
                "gps_review_reasons": gps_review_reasons,
                "imagekit_file_id": saved_media.get("image_id"),
                "street_address": street_address,
                "street_name": (geocoded or {}).get("street_name") or (geocoded or {}).get("road"),
                "landmark": (geocoded or {}).get("landmark"),
                "ward": (geocoded or {}).get("ward"),
                "city": (geocoded or {}).get("city"),
                "latitude_dms": decimal_to_dms(float(latitude), lat=True),
                "longitude_dms": decimal_to_dms(float(longitude), lat=False),
                "geocode_display_name": (geocoded or {}).get("display_name"),
                "local_path": saved_media.get("local_path"),
                "gps_accuracy_meters": float(accuracy) if accuracy is not None else None,
                "location_details": {
                    "street_name": (geocoded or {}).get("street_name") or (geocoded or {}).get("road"),
                    "landmark": (geocoded or {}).get("landmark"),
                    "ward": (geocoded or {}).get("ward"),
                    "city": (geocoded or {}).get("city"),
                    "state": (geocoded or {}).get("state"),
                    "country": (geocoded or {}).get("country"),
                },
            },
            is_before_image=True,
            is_after_image=False,
            storage_bucket=saved_media["storage_bucket"],
            cdn_url=saved_media["cdn_url"],
            thumbnail_path=saved_media["thumbnail_path"],
            processing_status=MEDIA_PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        db.session.add(media)
        db.session.commit()

        # Phase 6 — enqueue background YOLO analysis (non-blocking).
        analysis_task = None
        try:
            from app.services.task_service import submit_inspection_analysis

            analysis_task = submit_inspection_analysis(
                inspection_id=inspection.id,
                media_id=media.id,
            )
        except Exception:
            logger.exception(
                "Failed to enqueue analysis for inspection %s",
                inspection.id,
            )

        inspection._analysis_task = analysis_task  # ephemeral for response helpers
        return inspection

    except Exception:
        db.session.rollback()
        raise



def inspection_upload_payload(inspection) -> dict:
    """JSON payload returned by the upload API."""
    media = inspection.media_items[0] if inspection.media_items else None
    ward_name = None
    if inspection.ward_id and not offline_mode_enabled():
        try:
            from app.models.ward import Ward

            ward = db.session.get(Ward, inspection.ward_id)
            ward_name = ward.name if ward else None
        except Exception:
            ward_name = None

    local_path = None
    if media is not None:
        exif = getattr(media, "exif_data", None) or {}
        if isinstance(exif, dict):
            local_path = exif.get("local_path")

    return {
        "inspection_id": inspection.id,
        "inspection_code": inspection.inspection_code,
        "location_name": inspection.location_name,
        "address": inspection.location_name,
        "gps": {
            "lat": float(inspection.location_latitude)
            if inspection.location_latitude is not None
            else None,
            "lng": float(inspection.location_longitude)
            if inspection.location_longitude is not None
            else None,
        },
        "image_url": (media.cdn_url or media.file_path) if media else None,
        "local_path": local_path or (getattr(media, "file_path", None) if media else None),
        "media_id": media.id if media else None,
        "ward_assigned": ward_name,
        "ward_id": inspection.ward_id,
        "status": inspection.status,
        "analysis_task": getattr(inspection, "_analysis_task", None),
    }
