import mimetypes
import uuid
from datetime import datetime
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.utils.file_validation import (
    get_media_type,
    safe_original_filename,
)
from app.utils.image_metadata import get_image_dimensions


def save_phase4_temp_media(
    uploaded_file: FileStorage,
    inspection_code: str
) -> dict:
    original_name = safe_original_filename(uploaded_file.filename)

    if not original_name:
        raise ValueError("The uploaded file name is invalid.")

    media_type = get_media_type(original_name)
    extension = Path(original_name).suffix.lower()

    generated_name = (
        f"{uuid.uuid4().hex}_{Path(original_name).stem[:80]}{extension}"
    )

    storage_directory = (
        Path(current_app.config["TEMP_UPLOAD_ROOT"])
        / inspection_code
    )

    storage_directory.mkdir(parents=True, exist_ok=True)

    saved_file_path = storage_directory / generated_name

    uploaded_file.save(saved_file_path)

    file_size = saved_file_path.stat().st_size

    mime_type = (
        uploaded_file.mimetype
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )

    width = None
    height = None

    if media_type == "image":
        width, height = get_image_dimensions(saved_file_path)

    return {
        "file_name": original_name,
        "file_path": str(saved_file_path),
        "file_size_bytes": file_size,
        "mime_type": mime_type,
        "media_type": media_type,
        "resolution_width": width,
        "resolution_height": height,
        "duration_seconds": None,
        "captured_at": datetime.now(),
        "storage_bucket": "local-phase4-temp",
        "cdn_url": None,
        "thumbnail_path": None,
    }