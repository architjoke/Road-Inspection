"""ImageKit CDN storage for inspection media.

Credentials come from municipal_ai_system/.env via Flask config:
IMAGEKIT_PUBLIC_KEY, IMAGEKIT_PRIVATE_KEY, IMAGEKIT_URL_ENDPOINT.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)

_PLACEHOLDER_TOKENS = (
    "xxxxxxxx",
    "your_imagekit_id",
    "your-imagekit-id",
)


def _cfg(name: str) -> str:
    value = current_app.config.get(name)
    if value is None or str(value).strip() == "":
        value = os.getenv(name, "")
    return str(value or "").strip()


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in _PLACEHOLDER_TOKENS)


def _credentials() -> tuple[str, str, str]:
    return (
        _cfg("IMAGEKIT_PRIVATE_KEY"),
        _cfg("IMAGEKIT_PUBLIC_KEY"),
        _cfg("IMAGEKIT_URL_ENDPOINT"),
    )


def _client():
    from imagekitio import ImageKit

    private_key, public_key, url_endpoint = _credentials()
    if not private_key or _is_placeholder(private_key):
        raise RuntimeError(
            "IMAGEKIT_PRIVATE_KEY is not configured. "
            "Set ImageKit credentials in municipal_ai_system/.env."
        )

    try:
        return ImageKit(
            private_key=private_key,
            public_key=public_key or None,
            url_endpoint=url_endpoint or None,
        )
    except TypeError:
        return ImageKit(private_key=private_key)


def is_imagekit_configured() -> bool:
    private_key, public_key, url_endpoint = _credentials()
    if not private_key or not public_key or not url_endpoint:
        return False
    if _is_placeholder(private_key) or _is_placeholder(public_key) or _is_placeholder(
        url_endpoint
    ):
        return False
    return True


def _dated_folder(base_folder: str = "inspections") -> str:
    now = datetime.utcnow()
    return f"/{base_folder.strip('/')}/{now.year}/{now.month:02d}/{now.day:02d}"


def _do_upload(client, payload, raw_name: str, destination: str, tag_list: list[str]):
    kwargs = {
        "file": payload,
        "file_name": raw_name,
        "folder": destination,
        "tags": tag_list or None,
        "use_unique_file_name": True,
    }
    files_api = getattr(client, "files", None)
    if files_api is not None and hasattr(files_api, "upload"):
        return files_api.upload(**kwargs)
    if hasattr(client, "upload"):
        return client.upload(**kwargs)
    raise RuntimeError("Unsupported ImageKit SDK: no upload method found.")


def upload_to_imagekit(
    file: FileStorage | bytes | Path | str,
    folder: str = "inspections",
    tags: list[str] | None = None,
    file_name: str | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """
    Upload a file to ImageKit.

    Returns:
        {'image_id': '...', 'url': 'https://ik.imagekit.io/...', 'thumbnail_url': '...'}
    """
    if isinstance(file, FileStorage):
        raw_name = file_name or file.filename or "upload.bin"
        payload = file.stream
        if hasattr(payload, "seek"):
            payload.seek(0)
    elif isinstance(file, (str, Path)):
        path = Path(file)
        raw_name = file_name or path.name
        payload = path.open("rb")
    else:
        raw_name = file_name or "upload.bin"
        payload = file

    destination = _dated_folder(folder)
    tag_list = [str(tag) for tag in (tags or []) if tag]
    last_error: Exception | None = None

    try:
        for attempt in range(max_retries + 1):
            try:
                if hasattr(payload, "seek"):
                    payload.seek(0)

                client = _client()
                result = _do_upload(client, payload, raw_name, destination, tag_list)

                file_id = getattr(result, "file_id", None) or getattr(result, "fileId", None)
                url = getattr(result, "url", None)
                thumbnail = getattr(result, "thumbnail_url", None) or getattr(
                    result, "thumbnailUrl", None
                )

                if not url:
                    raise RuntimeError("ImageKit upload returned no URL.")

                return {
                    "image_id": file_id,
                    "url": url,
                    "thumbnail_url": thumbnail or get_transformed_url(
                        url, {"width": 400, "height": 300, "crop": "at_max"}
                    ),
                    "folder": destination,
                    "name": getattr(result, "name", raw_name),
                }
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "ImageKit upload attempt %s failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)

        raise RuntimeError(
            f"ImageKit upload failed after retries: {last_error}"
        ) from last_error
    finally:
        if isinstance(file, (str, Path)) and hasattr(payload, "close"):
            payload.close()


def get_image_details(image_id: str) -> dict[str, Any]:
    """Return ImageKit file metadata."""
    client = _client()
    details = client.files.get(image_id)

    return {
        "image_id": getattr(details, "file_id", image_id),
        "name": getattr(details, "name", None),
        "url": getattr(details, "url", None),
        "size": getattr(details, "size", None),
        "format": getattr(details, "file_type", None)
        or getattr(details, "fileType", None),
        "width": getattr(details, "width", None),
        "height": getattr(details, "height", None),
        "created_at": getattr(details, "created_at", None)
        or getattr(details, "createdAt", None),
    }


def get_transformed_url(image_url: str, transformations: dict | None = None) -> str:
    """
    Build a transformed ImageKit URL.

    Example transformations: {'width': 800, 'height': 600, 'quality': 80, 'crop': 'at_max'}
    """
    if not image_url:
        return image_url

    endpoint = (_cfg("IMAGEKIT_URL_ENDPOINT") or "").rstrip("/")
    transforms = transformations or {}

    parts: list[str] = []
    if "width" in transforms:
        parts.append(f"w-{transforms['width']}")
    if "height" in transforms:
        parts.append(f"h-{transforms['height']}")
    if "quality" in transforms:
        parts.append(f"q-{transforms['quality']}")
    if transforms.get("crop"):
        parts.append(f"c-{transforms['crop']}")
    if transforms.get("auto_format"):
        parts.append("f-auto")

    if not parts:
        return image_url

    transform_path = ",".join(parts)

    if "/tr:" in image_url:
        return image_url

    if endpoint and image_url.startswith(endpoint):
        relative = image_url[len(endpoint):].lstrip("/")
        return f"{endpoint}/tr:{transform_path}/{relative}"

    # Generic ImageKit path insertion before the file path segment.
    marker = "imagekit.io/"
    idx = image_url.find(marker)
    if idx == -1:
        return image_url

    after = image_url[idx + len(marker):]
    slash = after.find("/")
    if slash == -1:
        return image_url

    host = image_url[: idx + len(marker) + slash]
    path = after[slash + 1 :]
    return f"{host}/tr:{transform_path}/{path}"


def delete_from_imagekit(image_id: str) -> None:
    """Delete a file from ImageKit by file_id."""
    if not image_id:
        return

    client = _client()
    client.files.delete(image_id)
    logger.info("Deleted ImageKit file %s", image_id)
