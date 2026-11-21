"""GPS helpers: browser coordinates + EXIF extraction."""

from __future__ import annotations

import logging
import math
import os
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, IFD

logger = logging.getLogger(__name__)

# Approximate India bounding box used as a soft municipal sanity check.
DEFAULT_BOUNDS = {
    "min_lat": 6.0,
    "max_lat": 38.0,
    "min_lng": 68.0,
    "max_lng": 98.0,
}

# Browser geolocation on a PC comes from Wi-Fi/IP and is often +/-500 m or worse.
# Above REVIEW the inspection is flagged; above REJECT it is refused (pin on map).
DEFAULT_REVIEW_ACCURACY_M = 100.0
DEFAULT_REJECT_ACCURACY_M = 5000.0
# Browser vs photo GPS disagreement worth logging (photo GPS is still used).
BROWSER_EXIF_MISMATCH_M = 200.0


def _cfg_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get(name) is not None:
            return float(current_app.config[name])
    except Exception:
        pass
    return default


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, h)))


def parse_coordinate(value: str, coordinate_name: str) -> Decimal:
    try:
        coordinate = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(
            f"{coordinate_name} must be a valid number."
        ) from error

    return coordinate


def validate_latitude(latitude: Decimal) -> Decimal:
    if latitude < Decimal("-90") or latitude > Decimal("90"):
        raise ValueError("Latitude must be between -90 and 90.")

    return latitude


def validate_longitude(longitude: Decimal) -> Decimal:
    if longitude < Decimal("-180") or longitude > Decimal("180"):
        raise ValueError("Longitude must be between -180 and 180.")

    return longitude


def parse_accuracy(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None

    try:
        accuracy = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(
            "GPS accuracy must be a valid number."
        ) from error

    if accuracy < Decimal("0"):
        raise ValueError("GPS accuracy cannot be negative.")

    return accuracy


def get_browser_gps(
    latitude_value: str,
    longitude_value: str,
    accuracy_value: str | None
) -> tuple[Decimal, Decimal, Decimal | None]:
    latitude = validate_latitude(
        parse_coordinate(latitude_value, "Latitude")
    )

    longitude = validate_longitude(
        parse_coordinate(longitude_value, "Longitude")
    )

    accuracy = parse_accuracy(accuracy_value)

    return latitude, longitude, accuracy


def convert_dms_to_decimal(
    degrees,
    minutes,
    seconds,
    direction: str | None,
) -> float:
    """Convert EXIF DMS (degrees/minutes/seconds) to decimal degrees."""

    def _to_float(value) -> float:
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            denom = float(value.denominator) or 1.0
            return float(value.numerator) / denom
        if isinstance(value, (tuple, list)) and len(value) == 2:
            denom = float(value[1]) or 1.0
            return float(value[0]) / denom
        return float(value)

    decimal = (
        _to_float(degrees)
        + (_to_float(minutes) / 60.0)
        + (_to_float(seconds) / 3600.0)
    )

    if direction and str(direction).upper() in {"S", "W"}:
        decimal = -abs(decimal)

    return decimal


def _gps_ifd_to_dict(gps_ifd: dict) -> dict:
    named = {}
    for key, value in gps_ifd.items():
        named[GPSTAGS.get(key, key)] = value
    return named


def _extract_from_pil_image(image: Image.Image) -> dict | None:
    try:
        exif = image.getexif()
    except Exception:
        return None

    if not exif:
        return None

    try:
        gps_ifd = exif.get_ifd(IFD.GPSInfo)
    except Exception:
        gps_ifd = None

    if not gps_ifd:
        # Older Pillow path
        gps_raw = exif.get(34853)
        if not isinstance(gps_raw, dict):
            return None
        gps_data = _gps_ifd_to_dict(gps_raw)
    else:
        gps_data = _gps_ifd_to_dict(gps_ifd)

    lat_values = gps_data.get("GPSLatitude")
    lat_ref = gps_data.get("GPSLatitudeRef")
    lng_values = gps_data.get("GPSLongitude")
    lng_ref = gps_data.get("GPSLongitudeRef")

    if not lat_values or not lng_values:
        return None

    try:
        latitude = convert_dms_to_decimal(
            lat_values[0], lat_values[1], lat_values[2], lat_ref
        )
        longitude = convert_dms_to_decimal(
            lng_values[0], lng_values[1], lng_values[2], lng_ref
        )
    except (TypeError, ValueError, IndexError) as exc:
        logger.warning("Failed to convert EXIF GPS DMS values: %s", exc)
        return None

    accuracy = "high"
    if latitude == 0 and longitude == 0:
        return None

    accuracy_meters = None
    h_error = gps_data.get("GPSHPositioningError")
    if h_error is not None:
        try:
            if hasattr(h_error, "numerator"):
                accuracy_meters = float(h_error.numerator) / float(h_error.denominator or 1)
            elif isinstance(h_error, (tuple, list)) and len(h_error) == 2:
                accuracy_meters = float(h_error[0]) / float(h_error[1] or 1)
            else:
                accuracy_meters = float(h_error)
        except (TypeError, ValueError, ZeroDivisionError):
            accuracy_meters = None

    altitude = None
    alt_values = gps_data.get("GPSAltitude")
    if alt_values is not None:
        try:
            if hasattr(alt_values, "numerator"):
                altitude = float(alt_values.numerator) / float(alt_values.denominator or 1)
            elif isinstance(alt_values, (tuple, list)) and len(alt_values) == 2:
                altitude = float(alt_values[0]) / float(alt_values[1] or 1)
            else:
                altitude = float(alt_values)
            alt_ref = gps_data.get("GPSAltitudeRef")
            if alt_ref in (1, "1", b"\x01"):
                altitude = -abs(altitude)
        except (TypeError, ValueError, ZeroDivisionError):
            altitude = None

    from app.utils.location_format import decimal_to_dms

    return {
        "latitude": round(latitude, 8),
        "longitude": round(longitude, 8),
        "latitude_dms": decimal_to_dms(latitude, lat=True),
        "longitude_dms": decimal_to_dms(longitude, lat=False),
        "altitude_meters": altitude,
        "accuracy": accuracy,
        "accuracy_meters": accuracy_meters,
        "source": "exif",
    }


def extract_gps_from_image(image_file_or_path) -> dict | None:
    """
    Extract GPS from image EXIF metadata.

    Returns: {'latitude': 27.0238, 'longitude': 79.0215, 'accuracy': 'high'}
    or None if no GPS data is present.
    """
    try:
        if isinstance(image_file_or_path, (str, Path)):
            with Image.open(image_file_or_path) as image:
                return _extract_from_pil_image(image)

        if hasattr(image_file_or_path, "read"):
            stream: BinaryIO = image_file_or_path
            position = None
            if hasattr(stream, "tell"):
                try:
                    position = stream.tell()
                except Exception:
                    position = None

            data = stream.read()
            if position is not None and hasattr(stream, "seek"):
                stream.seek(position)

            with Image.open(BytesIO(data)) as image:
                return _extract_from_pil_image(image)

        with Image.open(BytesIO(image_file_or_path)) as image:
            return _extract_from_pil_image(image)

    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("GPS EXIF extraction failed: %s", exc)
        return None


def extract_gps_from_video(video_file_or_path) -> dict | None:
    """
    Read the phone-recorded GPS (ISO 6709) from the MP4/MOV container metadata.
    Returns the same dict format as extract_gps_from_image.
    """
    path: Path | None = None
    cleanup = False

    try:
        if isinstance(video_file_or_path, (str, Path)):
            path = Path(video_file_or_path)
        else:
            import tempfile

            suffix = ".mp4"
            if hasattr(video_file_or_path, "filename") and video_file_or_path.filename:
                suffix = Path(video_file_or_path.filename).suffix or ".mp4"

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            cleanup = True
            if hasattr(video_file_or_path, "read"):
                if hasattr(video_file_or_path, "seek"):
                    video_file_or_path.seek(0)
                tmp.write(video_file_or_path.read())
                if hasattr(video_file_or_path, "seek"):
                    video_file_or_path.seek(0)
            else:
                tmp.write(video_file_or_path)
            tmp.close()
            path = Path(tmp.name)

        # Phones store video GPS as an ISO 6709 string in the MP4/MOV metadata
        # (Android "\xa9xyz" atom, iPhone "com.apple.quicktime.location.ISO6709").
        # Re-encoding a frame with OpenCV would drop all metadata, so read the container.
        return extract_iso6709_from_video(path)

    except Exception as exc:
        logger.warning("Video GPS extraction failed: %s", exc)
        return None
    finally:
        if cleanup and path and path.exists():
            try:
                path.unlink()
            except OSError:
                pass


_ISO6709 = re.compile(
    rb"([+-]\d{2}(?:\.\d+)?)([+-]\d{3}(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/"
)
_VIDEO_SCAN_BYTES = 8 * 1024 * 1024


def extract_iso6709_from_video(path: str | Path) -> dict | None:
    """Find the phone-recorded GPS position in an MP4/MOV container."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(_VIDEO_SCAN_BYTES)
        tail = b""
        if size > _VIDEO_SCAN_BYTES:
            fh.seek(max(0, size - _VIDEO_SCAN_BYTES))
            tail = fh.read()
    # The moov (metadata) box is usually at the end of phone recordings.
    for blob in (tail, head):
        for marker in (b"\xa9xyz", b"ISO6709"):
            start = blob.find(marker)
            while start != -1:
                match = _ISO6709.search(blob, start, start + 600)
                if match:
                    lat, lng = float(match.group(1)), float(match.group(2))
                    if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0.0, 0.0):
                        from app.utils.location_format import decimal_to_dms

                        return {
                            "latitude": round(lat, 8),
                            "longitude": round(lng, 8),
                            "latitude_dms": decimal_to_dms(lat, lat=True),
                            "longitude_dms": decimal_to_dms(lng, lat=False),
                            "altitude_meters": None,
                            "accuracy": "high",
                            "accuracy_meters": None,
                            "source": "video_metadata",
                        }
                start = blob.find(marker, start + 1)
    return None


def validate_gps_bounds(
    latitude: float,
    longitude: float,
    ward_bounds: dict | None = None,
) -> bool:
    """
    Soft validation that coordinates are plausible for the municipality.
    Flags clearly wrong values such as (0, 0) or outside India.
    """
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return False

    if lat == 0.0 and lng == 0.0:
        return False

    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return False

    bounds = ward_bounds or DEFAULT_BOUNDS
    return (
        bounds["min_lat"] <= lat <= bounds["max_lat"]
        and bounds["min_lng"] <= lng <= bounds["max_lng"]
    )


def resolve_inspection_gps_details(
    form_data,
    uploaded_file=None,
    media_type: str | None = None,
) -> dict:
    """
    Decide where an inspection is, and whether a human should double-check it.

    Priority (most to least trustworthy for *where the damage is*):
      1. Manual map pin / typed coordinates (``gps_manual_override``)
      2. GPS embedded in the uploaded photo (EXIF) or video (ISO 6709)
      3. Browser geolocation. On a PC this is Wi-Fi/IP based (usually the
         office), so it is flagged above +/-100 m and refused above +/-5 km.
    """
    lat_raw = (form_data.get("location_latitude") or "").strip()
    lng_raw = (form_data.get("location_longitude") or "").strip()
    accuracy_raw = form_data.get("gps_accuracy_meters")
    manual = bool(form_data.get("gps_manual_override"))
    review_m = _cfg_float("GPS_REVIEW_ACCURACY_M", DEFAULT_REVIEW_ACCURACY_M)
    reject_m = _cfg_float("GPS_REJECT_ACCURACY_M", DEFAULT_REJECT_ACCURACY_M)

    form_coords = None
    if lat_raw and lng_raw:
        form_coords = get_browser_gps(lat_raw, lng_raw, accuracy_raw)

    extracted = None
    if uploaded_file is not None and not (manual and form_coords):
        try:
            if (media_type or "image") == "video":
                extracted = extract_gps_from_video(uploaded_file)
            else:
                extracted = extract_gps_from_image(uploaded_file)
        except Exception:
            logger.exception("Embedded GPS extraction failed")
            extracted = None

    reasons: list[str] = []
    browser_distance = None

    if manual and form_coords:
        latitude, longitude, accuracy = form_coords
        source = "manual"
    elif extracted:
        latitude = validate_latitude(Decimal(str(extracted["latitude"])))
        longitude = validate_longitude(Decimal(str(extracted["longitude"])))
        acc = extracted.get("accuracy_meters")
        accuracy = Decimal(str(round(acc, 2))) if acc is not None else None
        source = extracted.get("source", "exif")
        if form_coords:
            browser_distance = haversine_m(
                float(latitude), float(longitude), float(form_coords[0]), float(form_coords[1])
            )
            if browser_distance > BROWSER_EXIF_MISMATCH_M:
                logger.info(
                    "Browser location is %.0f m from the photo's GPS; using photo GPS",
                    browser_distance,
                )
        if accuracy is not None and float(accuracy) > review_m:
            reasons.append(f"photo GPS accuracy +/-{float(accuracy):.0f} m")
    elif form_coords:
        latitude, longitude, accuracy = form_coords
        source = "browser"
        if accuracy is None:
            reasons.append("browser location without accuracy")
        elif float(accuracy) > reject_m:
            raise ValueError(
                f"Browser location is too imprecise (+/-{float(accuracy) / 1000:.1f} km), "
                "which usually means a PC without GPS. Pin the damage on the map, or "
                "upload the original photo (not a WhatsApp copy) so its GPS is used."
            )
        elif float(accuracy) > review_m:
            reasons.append(
                f"browser location +/-{float(accuracy):.0f} m "
                "(PC/Wi-Fi estimate, may not be the damage site)"
            )
    else:
        raise ValueError(
            "GPS coordinates are required. Pin the location on the map, capture "
            "browser location on a phone, or upload an original photo with GPS "
            "(WhatsApp removes photo GPS)."
        )

    if not validate_gps_bounds(float(latitude), float(longitude)):
        reasons.append("coordinates outside India")

    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "source": source,
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "embedded_gps_found": extracted is not None,
        "browser_distance_m": round(browser_distance, 1) if browser_distance is not None else None,
    }


def resolve_inspection_gps(
    form_data,
    uploaded_file=None,
    media_type: str | None = None,
) -> tuple[Decimal, Decimal, Decimal | None, str, bool]:
    """Backward-compatible tuple: latitude, longitude, accuracy, source, needs_review."""
    d = resolve_inspection_gps_details(form_data, uploaded_file, media_type)
    return d["latitude"], d["longitude"], d["accuracy"], d["source"], d["needs_review"]
