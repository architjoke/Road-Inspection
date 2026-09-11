"""Media persistence: ImageKit when configured, local temp otherwise."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.utils.file_validation import get_media_type, safe_original_filename
from app.utils.image_metadata import get_image_dimensions

logger = logging.getLogger(__name__)


def save_phase4_temp_media(
    uploaded_file: FileStorage,
    inspection_code: str,
    tags: list[str] | None = None,
) -> dict:
    """
    Persist uploaded media.

    Prefer ImageKit CDN; fall back to local TEMP_UPLOAD_ROOT when ImageKit
    credentials are missing (local/dev / college-wifi offline work).
    """
    original_name = safe_original_filename(uploaded_file.filename)

    if not original_name:
        raise ValueError("The uploaded file name is invalid.")

    media_type = get_media_type(original_name)
    extension = Path(original_name).suffix.lower()
    generated_name = (
        f"{uuid.uuid4().hex}_{Path(original_name).stem[:80]}{extension}"
    )

    mime_type = (
        uploaded_file.mimetype
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )

    width = height = None
    duration_seconds = None
    storage_bucket = "local-phase4-temp"
    cdn_url = None
    thumbnail_path = None
    file_path: str
    file_size: int
    image_id = None

    # Always keep a local copy for EXIF/video tooling and offline viewing.
    storage_directory = (
        Path(current_app.config["TEMP_UPLOAD_ROOT"]) / inspection_code
    )
    storage_directory.mkdir(parents=True, exist_ok=True)
    saved_file_path = storage_directory / generated_name

    if hasattr(uploaded_file.stream, "seek"):
        uploaded_file.stream.seek(0)
    uploaded_file.save(saved_file_path)
    file_size = saved_file_path.stat().st_size
    file_path = str(saved_file_path)

    if media_type == "image":
        width, height = get_image_dimensions(saved_file_path)
    elif media_type == "video":
        try:
            from app.services.video_service import validate_video

            meta = validate_video(saved_file_path)
            duration_seconds = meta.get("duration_sec")
            resolution = meta.get("resolution") or ""
            if "x" in resolution and resolution != "unknown":
                parts = resolution.split("x")
                width, height = int(parts[0]), int(parts[1])
        except Exception:
            logger.exception("Video metadata inspection failed for %s", original_name)

    from app.services.imagekit_service import is_imagekit_configured, upload_to_imagekit
    from app.services.offline_store import offline_mode_enabled

    # Offline/TESTING: keep local storage only (no ImageKit / internet).
    if is_imagekit_configured() and not offline_mode_enabled():
        try:
            if hasattr(uploaded_file.stream, "seek"):
                uploaded_file.stream.seek(0)

            uploaded = upload_to_imagekit(
                file=saved_file_path,
                folder="inspections",
                tags=tags or [inspection_code],
                file_name=generated_name,
            )
            image_id = uploaded.get("image_id")
            cdn_url = uploaded.get("url")
            thumbnail_path = uploaded.get("thumbnail_url")
            storage_bucket = "imagekit"
            # Prefer CDN URL as the canonical file_path for remote access.
            if cdn_url:
                file_path = cdn_url
            logger.info(
                "Uploaded %s to ImageKit as %s",
                original_name,
                image_id,
            )
        except Exception:
            logger.exception(
                "ImageKit upload failed for inspection %s; keeping local file.",
                inspection_code,
            )

    return {
        "file_name": original_name,
        "file_path": file_path,
        "file_size_bytes": file_size,
        "mime_type": mime_type,
        "media_type": media_type,
        "resolution_width": width,
        "resolution_height": height,
        "duration_seconds": duration_seconds,
        "captured_at": datetime.now(),
        "storage_bucket": storage_bucket,
        "cdn_url": cdn_url,
        "thumbnail_path": thumbnail_path,
        "image_id": image_id,
        "local_path": str(saved_file_path),
    }
