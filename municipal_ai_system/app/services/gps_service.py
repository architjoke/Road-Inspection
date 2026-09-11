"""GPS helpers: browser coordinates + EXIF extraction."""

from __future__ import annotations

import logging
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

    return {
        "latitude": round(latitude, 8),
        "longitude": round(longitude, 8),
        "accuracy": accuracy,
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
    Try video container metadata first, then first-frame EXIF via OpenCV.
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

        # Most phone videos do not embed GPS in a standard EXIF IFD.
        # Fall back to first-frame extraction when OpenCV is available.
        try:
            import cv2  # type: ignore
        except ImportError:
            logger.info("OpenCV not installed; skipping video frame GPS extraction.")
            return None

        capture = cv2.VideoCapture(str(path))
        ok, frame = capture.read()
        capture.release()

        if not ok or frame is None:
            return None

        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            return None

        return extract_gps_from_image(BytesIO(buffer.tobytes()))

    except Exception as exc:
        logger.warning("Video GPS extraction failed: %s", exc)
        return None
    finally:
        if cleanup and path and path.exists():
            try:
                path.unlink()
            except OSError:
                pass


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


def resolve_inspection_gps(
    form_data,
    uploaded_file=None,
    media_type: str | None = None,
) -> tuple[Decimal, Decimal, Decimal | None, str, bool]:
    """
    Resolve GPS for an upload.

    Priority:
      1. Explicit form coordinates (browser or manual override)
      2. EXIF / video metadata
    Returns: latitude, longitude, accuracy, source, needs_review
    """
    lat_raw = (form_data.get("location_latitude") or "").strip()
    lng_raw = (form_data.get("location_longitude") or "").strip()
    accuracy_raw = form_data.get("gps_accuracy_meters")

    if lat_raw and lng_raw:
        latitude, longitude, accuracy = get_browser_gps(
            lat_raw, lng_raw, accuracy_raw
        )
        source = "manual" if form_data.get("gps_manual_override") else "browser"
        needs_review = not validate_gps_bounds(float(latitude), float(longitude))
        return latitude, longitude, accuracy, source, needs_review

    extracted = None
    if uploaded_file is not None:
        kind = media_type or "image"
        if kind == "video":
            extracted = extract_gps_from_video(uploaded_file)
        else:
            extracted = extract_gps_from_image(uploaded_file)

    if extracted:
        latitude = validate_latitude(Decimal(str(extracted["latitude"])))
        longitude = validate_longitude(Decimal(str(extracted["longitude"])))
        needs_review = not validate_gps_bounds(float(latitude), float(longitude))
        return latitude, longitude, None, extracted.get("source", "exif"), needs_review

    raise ValueError(
        "GPS coordinates are required. Capture browser location, provide "
        "manual latitude/longitude, or upload a photo with EXIF GPS."
    )
