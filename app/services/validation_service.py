"""Upload validation for inspection images and videos."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.utils.file_validation import get_media_type


IMAGE_MAX_BYTES = 50 * 1024 * 1024
VIDEO_MAX_BYTES = 500 * 1024 * 1024
MIN_WIDTH = 1280
MIN_HEIGHT = 720
# Short-side pixel floors. Below REJECT the detector cannot see road damage at
# all (e.g. 180x168 thumbnails); below RECOMMENDED results are flagged.
MIN_SHORT_SIDE_REJECT = 480
MIN_SHORT_SIDE_RECOMMENDED = 720

ALLOWED_MIME = {
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "mp4": {"video/mp4", "application/mp4", "video/mpeg"},
    "mov": {"video/quicktime", "video/mp4"},
}


def validate_upload(file: FileStorage, file_type: str | None = None) -> dict:
    """
    Validate an upload before storage.

    Returns: {'valid': True, ...} or {'valid': False, 'error': '...'}
    """
    if file is None or not file.filename:
        return {"valid": False, "error": "Please choose an image or video file."}

    if ".." in file.filename or file.filename.startswith(("/", "\\")):
        return {"valid": False, "error": "Invalid file name."}

    raw_name = Path(file.filename).name
    extension = Path(raw_name).suffix.lower().lstrip(".")
    allowed_images = current_app.config.get(
        "ALLOWED_IMAGE_EXTENSIONS", {"jpg", "jpeg", "png"}
    )
    allowed_videos = current_app.config.get(
        "ALLOWED_VIDEO_EXTENSIONS", {"mp4", "mov"}
    )
    allowed = set(allowed_images) | set(allowed_videos)

    if extension not in allowed:
        return {
            "valid": False,
            "error": "Unsupported file type. Allowed: JPG, JPEG, PNG, MP4, MOV.",
        }

    media_type = file_type or get_media_type(raw_name)
    if media_type == "unknown":
        return {"valid": False, "error": "Unable to determine media type."}

    # Content-length / stream size when available
    stream = file.stream
    size = None
    if hasattr(stream, "seek") and hasattr(stream, "tell"):
        try:
            position = stream.tell()
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(position)
        except Exception:
            size = None

    if size is None:
        content_length = getattr(file, "content_length", None)
        size = int(content_length) if content_length else 0

    max_bytes = IMAGE_MAX_BYTES if media_type == "image" else VIDEO_MAX_BYTES
    if size and size > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        actual_mb = size / (1024 * 1024)
        return {
            "valid": False,
            "error": (
                f"File too large: {actual_mb:.1f}MB > {limit_mb:.0f}MB"
            ),
        }

    declared_mime = (file.mimetype or "").lower()
    guessed_mime = (mimetypes.guess_type(raw_name)[0] or "").lower()
    expected = ALLOWED_MIME.get(extension, set())

    if declared_mime and expected and declared_mime not in expected:
        if declared_mime.split("/")[0] not in {"image", "video", "application"}:
            return {
                "valid": False,
                "error": (
                    f"MIME type {declared_mime} does not match .{extension}."
                ),
            }

    warnings: list[str] = []
    width = height = None
    if media_type == "image":
        resolution = check_image_resolution(file)
        if not resolution["valid"]:
            return {"valid": False, "error": resolution["error"]}
        width, height = resolution["width"], resolution["height"]
        if resolution.get("warning"):
            warnings.append(resolution["warning"])

    return {
        "valid": True,
        "media_type": media_type,
        "extension": extension,
        "size_bytes": size or 0,
        "mime_type": declared_mime or guessed_mime or "application/octet-stream",
        "resolution_width": width,
        "resolution_height": height,
        "warnings": warnings,
        "min_resolution_hint": f"{MIN_WIDTH}x{MIN_HEIGHT}",
        "safe_filename": raw_name,
    }


def check_image_resolution(file: FileStorage) -> dict:
    """Read image size from the upload stream (rewinds it) and apply the floors."""
    from PIL import Image, UnidentifiedImageError

    stream = file.stream
    position = stream.tell() if hasattr(stream, "tell") else 0
    try:
        with Image.open(stream) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        return {"valid": False, "error": "The image could not be read (corrupted or unsupported)."}
    finally:
        if hasattr(stream, "seek"):
            stream.seek(position)
    short = min(width, height)
    if short < MIN_SHORT_SIDE_REJECT:
        return {
            "valid": False,
            "width": width,
            "height": height,
            "error": (
                f"Image is only {width}x{height} px — too small to detect road damage "
                f"(need at least {MIN_SHORT_SIDE_REJECT} px on the short side; "
                f"{MIN_WIDTH}x{MIN_HEIGHT} recommended). Use the original photo, or send it "
                "on WhatsApp as a document instead of a photo."
            ),
        }
    warning = None
    if short < MIN_SHORT_SIDE_RECOMMENDED:
        warning = (
            f"Low resolution {width}x{height} px; detection and size estimates "
            f"may be unreliable ({MIN_WIDTH}x{MIN_HEIGHT} recommended)."
        )
    return {"valid": True, "width": width, "height": height, "warning": warning}


ALLOWED_REPORT_FORMATS = {"pdf", "csv", "excel", "xlsx", "json"}


def validate_coordinates(lat: object, lng: object) -> dict:
    """Strict lat/lng validation for API filters and geotags."""
    try:
        latitude = float(lat)
        longitude = float(lng)
    except (TypeError, ValueError):
        return {"valid": False, "error": "Latitude and longitude must be numbers."}

    if not (-90.0 <= latitude <= 90.0):
        return {"valid": False, "error": "Latitude must be between -90 and 90."}
    if not (-180.0 <= longitude <= 180.0):
        return {"valid": False, "error": "Longitude must be between -180 and 180."}

    return {
        "valid": True,
        "latitude": latitude,
        "longitude": longitude,
    }


def validate_report_format(fmt: object) -> dict:
    """Sanitize report export format parameter."""
    if fmt is None or fmt == "":
        return {"valid": True, "format": "pdf"}
    value = str(fmt).strip().lower()
    if value not in ALLOWED_REPORT_FORMATS:
        return {
            "valid": False,
            "error": "Unsupported format. Allowed: pdf, csv, excel, json.",
        }
    if value == "xlsx":
        value = "excel"
    return {"valid": True, "format": value}


def validate_positive_int(value: object, field: str = "id") -> dict:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return {"valid": False, "error": f"{field} must be an integer."}
    if number < 1:
        return {"valid": False, "error": f"{field} must be a positive integer."}
    return {"valid": True, "value": number}
