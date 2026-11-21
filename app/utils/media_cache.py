"""Local copies of inspection media for OpenCV / YOLO.

Uploads are stored on ImageKit and the local temp copy is eventually cleaned
up (or was written on another PC). Analysis and before/after verification need
a real file, so this fetches the original from the app's own ImageKit endpoint
into ``INSTANCE_DIR/media_cache`` when no local copy exists.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov"}


def _cache_root() -> Path:
    from app.utils.paths import instance_dir

    return instance_dir("media_cache")


def _allowed_host(url: str) -> bool:
    """Only fetch from the configured ImageKit endpoint (no arbitrary URLs)."""
    host = (urlparse(url).hostname or "").lower()
    if not host or urlparse(url).scheme != "https":
        return False
    try:
        from flask import current_app

        endpoint = current_app.config.get("IMAGEKIT_URL_ENDPOINT") or ""
    except Exception:
        endpoint = ""
    endpoint_host = (urlparse(endpoint).hostname or "").lower()
    return bool(endpoint_host) and host == endpoint_host


def remote_url(media) -> str | None:
    for attr in ("cdn_url", "file_path"):
        value = getattr(media, attr, None)
        if value and str(value).startswith("http"):
            return str(value)
    return None


def ensure_local_media(media) -> Path | None:
    """Local original file for ``media``; downloads from ImageKit if needed."""
    from app.utils.media_urls import resolve_original_path

    local = resolve_original_path(media)
    if local is not None:
        return local

    url = remote_url(media)
    media_id = getattr(media, "id", None)
    if not url or media_id is None:
        return None
    if not _allowed_host(url):
        logger.warning("Refusing to fetch media %s from non-ImageKit host %s", media_id, url)
        return None

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        suffix = ".jpg"
    target = _cache_root() / f"media_{media_id}{suffix}"
    if target.is_file() and target.stat().st_size > 0:
        return target

    import requests

    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            written = 0
            with open(tmp, "wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise ValueError("media larger than download limit")
                    fh.write(chunk)
        tmp.replace(target)
        logger.info("Fetched media %s from ImageKit into %s", media_id, target)
        return target
    except Exception as exc:
        logger.warning("Could not fetch media %s from ImageKit: %s", media_id, exc)
        tmp.unlink(missing_ok=True)
        return None
