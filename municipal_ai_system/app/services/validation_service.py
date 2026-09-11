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

    extension = Path(file.filename).suffix.lower().lstrip(".")
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

    media_type = file_type or get_media_type(file.filename)
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
    guessed_mime = (mimetypes.guess_type(file.filename)[0] or "").lower()
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
    # Resolution is checked after save in media_service to avoid consuming the stream twice.

    return {
        "valid": True,
        "media_type": media_type,
        "extension": extension,
        "size_bytes": size or 0,
        "mime_type": declared_mime or guessed_mime or "application/octet-stream",
        "resolution_width": None,
        "resolution_height": None,
        "warnings": warnings,
        "min_resolution_hint": f"{MIN_WIDTH}x{MIN_HEIGHT}",
    }
