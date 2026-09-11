"""In-memory inspection/media store for offline local acceptance (no RDS)."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class OfflineMedia:
    id: int
    inspection_id: int
    media_type: str
    file_path: str
    file_name: str
    file_size_bytes: int | None = None
    mime_type: str | None = None
    resolution_width: int | None = None
    resolution_height: int | None = None
    duration_seconds: float | None = None
    captured_at: datetime | None = None
    gps_latitude: Any = None
    gps_longitude: Any = None
    gps_accuracy_meters: Any = None
    exif_data: dict | None = None
    is_before_image: bool = True
    is_after_image: bool = False
    storage_bucket: str | None = None
    cdn_url: str | None = None
    thumbnail_path: str | None = None
    processing_status: str = "pending"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class OfflineInspection:
    id: int
    inspection_code: str
    municipality_id: int
    user_id: int
    ward_id: int | None = None
    inspection_date: datetime | None = None
    location_name: str | None = None
    location_latitude: Any = None
    location_longitude: Any = None
    location_geometry: Any = None
    status: str = "pending_analysis"
    inspection_type: str = "routine"
    notes: str | None = None
    weather_conditions: str | None = None
    lighting_conditions: str | None = None
    total_detections: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    media_items: list[OfflineMedia] = field(default_factory=list)
    _analysis_task: dict | None = None


class OfflineStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._inspections: dict[int, OfflineInspection] = {}
        self._media: dict[int, OfflineMedia] = {}
        self._next_inspection_id = 1
        self._next_media_id = 1

    def clear(self) -> None:
        with self._lock:
            self._inspections.clear()
            self._media.clear()
            self._next_inspection_id = 1
            self._next_media_id = 1

    def create_inspection(self, **fields) -> OfflineInspection:
        with self._lock:
            inspection_id = self._next_inspection_id
            self._next_inspection_id += 1
            inspection = OfflineInspection(id=inspection_id, **fields)
            self._inspections[inspection_id] = inspection
            return deepcopy(inspection)

    def add_media(self, inspection_id: int, **fields) -> OfflineMedia:
        with self._lock:
            inspection = self._inspections.get(inspection_id)
            if inspection is None:
                raise KeyError(f"Inspection {inspection_id} not found")
            media_id = self._next_media_id
            self._next_media_id += 1
            media = OfflineMedia(
                id=media_id,
                inspection_id=inspection_id,
                **fields,
            )
            self._media[media_id] = media
            inspection.media_items.append(media)
            return deepcopy(media)

    def list_inspections(self) -> list[OfflineInspection]:
        with self._lock:
            return [deepcopy(item) for item in self._inspections.values()]

    def get_inspection(self, inspection_id: int) -> OfflineInspection | None:
        with self._lock:
            item = self._inspections.get(inspection_id)
            return deepcopy(item) if item else None

    def get_media(self, media_id: int) -> OfflineMedia | None:
        with self._lock:
            item = self._media.get(media_id)
            return deepcopy(item) if item else None

    def update_inspection(self, inspection_id: int, **fields) -> None:
        with self._lock:
            item = self._inspections.get(inspection_id)
            if item is None:
                return
            for key, value in fields.items():
                if hasattr(item, key):
                    setattr(item, key, value)

    def update_media(self, media_id: int, **fields) -> None:
        with self._lock:
            item = self._media.get(media_id)
            if item is None:
                return
            for key, value in fields.items():
                if hasattr(item, key):
                    setattr(item, key, value)
            # keep inspection.media_items in sync
            for inspection in self._inspections.values():
                for idx, media in enumerate(inspection.media_items):
                    if media.id == media_id:
                        inspection.media_items[idx] = item


offline_store = OfflineStore()


def offline_mode_enabled() -> bool:
    try:
        from flask import current_app, has_app_context
        import os

        if has_app_context():
            if current_app.config.get("TESTING") or current_app.config.get("OFFLINE_MODE"):
                return True
            if not current_app.config.get("DATABASE_CONFIGURED", True):
                # Only treat as offline store when explicitly opted in
                return bool(current_app.config.get("OFFLINE_MODE"))
        return os.getenv("OFFLINE_MODE", "").lower() == "true"
    except Exception:
        import os

        return os.getenv("OFFLINE_MODE", "").lower() == "true"
