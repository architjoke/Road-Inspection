"""Create inspections with media, GPS, and optional ward assignment."""

from __future__ import annotations

import logging
from datetime import datetime

from geoalchemy2.elements import WKTElement

from app.extensions import db
from app.models.inspection import Inspection
from app.models.inspection_media import InspectionMedia
from app.services.gps_service import resolve_inspection_gps
from app.services.geocoding_service import resolve_location_label, reverse_geocode
from app.services.media_service import save_phase4_temp_media
from app.services.ward_service import find_ward_for_point
from app.utils.file_validation import get_media_type
from app.utils.identifiers import generate_inspection_code
from app.utils.status_constants import MEDIA_PENDING, PENDING_ANALYSIS

logger = logging.getLogger(__name__)


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

    latitude, longitude, accuracy, gps_source, needs_review = resolve_inspection_gps(
        form_data=form_data,
        uploaded_file=uploaded_file,
        media_type=media_type,
    )

    # Auto street address from GPS (Nominatim). Keep typed name if user provided one
    # that is more specific than a placeholder.
    geocoded = reverse_geocode(float(latitude), float(longitude))
    street_address = (geocoded or {}).get("address") if geocoded else None

    placeholder_names = {
        "",
        "field upload",
        "road inspection",
        "location",
        "unknown",
    }
    if not location_name or location_name.lower() in placeholder_names:
        location_name = resolve_location_label(
            float(latitude),
            float(longitude),
            fallback=location_name or None,
        )
    elif street_address and street_address.lower() not in location_name.lower():
        # Keep worker note + append resolved address for searchability
        if len(location_name) < 80:
            location_name = f"{location_name} — {street_address}"

    if not location_name:
        raise ValueError("Road or location name is required.")

    if needs_review:
        logger.warning(
            "GPS outside expected municipal bounds for user_id=%s "
            "(lat=%s, lng=%s, source=%s)",
            getattr(current_user, "pk", current_user.id),
            latitude,
            longitude,
            gps_source,
        )

    inspection_code = generate_inspection_code()

    ward = find_ward_for_point(
        float(latitude),
        float(longitude),
        municipality_id=municipality_id,
    )
    ward_id = ward.id if ward else None

    note_parts = []
    if notes:
        note_parts.append(notes)
    if gps_source == "exif":
        note_parts.append("[GPS from EXIF]")
    elif gps_source == "manual":
        note_parts.append("[GPS manual override]")
    if needs_review:
        note_parts.append("[Flagged for admin GPS review]")
    if street_address:
        note_parts.append(f"[Address: {street_address}]")


    try:
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
            notes=" ".join(note_parts) or None,
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
                "imagekit_file_id": saved_media.get("image_id"),
                "street_address": street_address,
                "geocode_display_name": (geocoded or {}).get("display_name"),
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

        # Phase 6/7 hook — AI detection queue (threading stub).
        _queue_analysis_stub(inspection.id)

        return inspection

    except Exception:
        db.session.rollback()
        raise


def _queue_analysis_stub(inspection_id: int) -> None:
    """Placeholder for Phase 6 background YOLO analysis."""
    logger.info(
        "Inspection %s queued for AI analysis (Phase 6 stub).",
        inspection_id,
    )


def inspection_upload_payload(inspection: Inspection) -> dict:
    """JSON payload returned by the upload API."""
    media = inspection.media_items[0] if inspection.media_items else None
    ward_name = None
    if inspection.ward_id:
        from app.models.ward import Ward

        ward = db.session.get(Ward, inspection.ward_id)
        ward_name = ward.name if ward else None

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
        "ward_assigned": ward_name,
        "ward_id": inspection.ward_id,
        "status": inspection.status,
    }
