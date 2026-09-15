"""Public URLs for inspection media (ImageKit CDN or local viewer)."""

from __future__ import annotations

from flask import url_for


def media_public_url(media) -> str | None:
    if media is None:
        return None
    cdn = getattr(media, "cdn_url", None)
    if cdn and str(cdn).startswith("http"):
        return str(cdn)
    path = getattr(media, "file_path", None)
    if path and str(path).startswith("http"):
        return str(path)
    media_id = getattr(media, "id", None)
    if media_id:
        try:
            return url_for("inspections.view_media", media_id=media_id)
        except Exception:
            return None
    return None


def is_before_media(media) -> bool:
    if getattr(media, "is_after_image", False):
        return False
    if getattr(media, "is_before_image", None) is False:
        return False
    return True


def get_before_media(inspection):
    items = list(getattr(inspection, "media_items", None) or [])
    for item in items:
        if is_before_media(item) and (getattr(item, "media_type", "") or "").lower() in {
            "image",
            "photo",
            "jpg",
            "jpeg",
            "png",
        }:
            return item
    for item in items:
        if (getattr(item, "media_type", "") or "").lower() == "image":
            return item
    return items[0] if items else None
