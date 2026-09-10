from datetime import datetime

from geoalchemy2.elements import WKTElement

from app.extensions import db
from app.models.inspection import Inspection
from app.models.inspection_media import InspectionMedia
from app.services.gps_service import get_browser_gps
from app.services.media_service import save_phase4_temp_media
from app.utils.identifiers import generate_inspection_code
from app.utils.status_constants import MEDIA_PENDING, PENDING_ANALYSIS


def create_inspection_with_media(
    current_user,
    form_data,
    uploaded_file
) -> Inspection:
    municipality_id = current_user.municipality_id

    if municipality_id is None:
        raise ValueError(
            "Your account is not assigned to a municipality. "
            "Please contact the Municipal Admin."
        )

    location_name = (form_data.get("location_name") or "").strip()
    notes = (form_data.get("notes") or "").strip()
    weather_conditions = (
        form_data.get("weather_conditions") or ""
    ).strip()
    lighting_conditions = (
        form_data.get("lighting_conditions") or ""
    ).strip()

    if not location_name:
        raise ValueError("Road or location name is required.")

    latitude, longitude, accuracy = get_browser_gps(
        form_data.get("location_latitude", ""),
        form_data.get("location_longitude", ""),
        form_data.get("gps_accuracy_meters", ""),
    )

    inspection_code = generate_inspection_code()

    try:
        inspection = Inspection(
            inspection_code=inspection_code,
            municipality_id=municipality_id,
            user_id=current_user.id,
            inspection_date=datetime.now(),
            location_name=location_name,
            location_latitude=latitude,
            location_longitude=longitude,
            location_geometry=WKTElement(
                f"POINT({longitude} {latitude})",
                srid=4326
            ),
            status=PENDING_ANALYSIS,
            inspection_type="routine",
            notes=notes or None,
            weather_conditions=weather_conditions or None,
            lighting_conditions=lighting_conditions or None,
            total_detections=0,
        )

        db.session.add(inspection)
        db.session.flush()

        saved_media = save_phase4_temp_media(
            uploaded_file=uploaded_file,
            inspection_code=inspection_code
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
            is_before_image=True,
            is_after_image=False,
            storage_bucket=saved_media["storage_bucket"],
            cdn_url=saved_media["cdn_url"],
            thumbnail_path=saved_media["thumbnail_path"],
            processing_status=MEDIA_PENDING,
        )

        db.session.add(media)
        db.session.commit()

        return inspection

    except Exception:
        db.session.rollback()
        raise