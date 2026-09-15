"""Public URLs for inspection media (ImageKit CDN or local viewer)."""

from __future__ import annotations

from pathlib import Path

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


def _annotated_candidates(source: Path) -> list[Path]:
    return [
        source.parent / f"{source.stem}_annotated.jpg",
        source.parent / f"{source.stem}_annotated_frame0.jpg",
        source.with_name(f"{source.stem}_annotated{source.suffix}"),
    ]


def resolve_annotated_path(media) -> Path | None:
    """Return local annotated JPEG path if the worker already wrote one."""
    if media is None:
        return None
    thumb = getattr(media, "thumbnail_path", None)
    if thumb and not str(thumb).startswith("http"):
        candidate = Path(str(thumb))
        if candidate.is_file() and "_annotated" in candidate.name.lower():
            return candidate

    roots: list[Path] = []
    file_path = getattr(media, "file_path", None)
    if file_path and not str(file_path).startswith("http"):
        roots.append(Path(str(file_path)))

    exif = getattr(media, "exif_data", None) or {}
    if isinstance(exif, dict):
        for key in ("local_path", "source_path", "original_path"):
            local = exif.get(key)
            if local and not str(local).startswith("http"):
                roots.append(Path(str(local)))

    seen: set[str] = set()
    for source in roots:
        key = str(source)
        if key in seen:
            continue
        seen.add(key)
        for candidate in _annotated_candidates(source):
            if candidate.is_file():
                return candidate
    return None


def resolve_original_path(media) -> Path | None:
    """Local original (non-annotated) file for SSIM compare."""
    if media is None:
        return None
    candidates: list[Path] = []
    file_path = getattr(media, "file_path", None)
    if file_path and not str(file_path).startswith("http"):
        candidates.append(Path(str(file_path)))
    exif = getattr(media, "exif_data", None) or {}
    if isinstance(exif, dict):
        for key in ("local_path", "source_path", "original_path"):
            local = exif.get(key)
            if local and not str(local).startswith("http"):
                candidates.append(Path(str(local)))
    for path in candidates:
        if path.is_file():
            return path
    return None


def annotated_media_url(media) -> str | None:
    if media is None or resolve_annotated_path(media) is None:
        return None
    media_id = getattr(media, "id", None)
    if not media_id:
        return None
    try:
        return url_for("inspections.view_annotated_media", media_id=media_id)
    except Exception:
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
