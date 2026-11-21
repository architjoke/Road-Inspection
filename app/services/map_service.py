"""Offline-safe map GeoJSON builders + Haversine radius (no RDS required)."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from app.utils.geo_utils import feature_collection


EARTH_RADIUS_M = 6371000.0


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters (offline spatial fallback)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _point_feature(lat: float, lng: float, properties: dict[str, Any]) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(lng), float(lat)]},
        "properties": properties,
    }


def _match_severity(value: Any, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    if value is None:
        return False
    return str(value).upper() in {s.upper() for s in allowed}


def _match_priority(value: Any, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    if value is None:
        return False
    return str(value) in {str(p) for p in allowed}


def offline_map_detections_geojson(
    *,
    municipality_id: int | None = None,
    severity: list[str] | None = None,
    ward_id: int | None = None,
    priority: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """
    Build FeatureCollection from in-memory detections + offline inspections.

    Relative depth is never exposed as metric. No PostGIS required.
    """
    from app.services.detection_service import list_memory_detections
    from app.services.offline_store import offline_store

    inspections = {
        item.id: item
        for item in offline_store.list_inspections()
        if municipality_id is None or item.municipality_id == municipality_id
    }
    features: list[dict] = []
    used_inspections: set[int] = set()

    for det in list_memory_detections():
        inspection = inspections.get(det.get("inspection_id"))
        if inspection is None:
            continue
        if ward_id is not None and inspection.ward_id != ward_id:
            continue
        if not _match_severity(det.get("severity"), severity):
            continue
        if not _match_priority(det.get("priority"), priority):
            continue

        created = det.get("created_at")
        if date_from or date_to:
            # created_at may be ISO string
            try:
                if isinstance(created, str):
                    created_dt = datetime.fromisoformat(created.replace("Z", ""))
                elif isinstance(created, datetime):
                    created_dt = created
                else:
                    created_dt = None
                if created_dt is not None:
                    if date_from and created_dt.date() < date_from:
                        continue
                    if date_to and created_dt.date() > date_to:
                        continue
            except Exception:
                pass

        lat = _safe_float(inspection.location_latitude)
        lng = _safe_float(inspection.location_longitude)
        if lat is None or lng is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue

        media = inspection.media_items[0] if inspection.media_items else None
        thumb = None
        if media is not None:
            thumb = media.thumbnail_path or media.cdn_url or media.file_path

        assessment = det.get("severity_assessment") or {}
        features.append(
            _point_feature(
                lat,
                lng,
                {
                    "feature_type": "detection",
                    "detection_id": det.get("detection_id"),
                    "inspection_id": inspection.id,
                    "inspection_code": inspection.inspection_code,
                    "damage_type": det.get("damage_type") or det.get("class_name"),
                    "severity": det.get("severity"),
                    "priority": det.get("priority"),
                    "severity_score": det.get("severity_score"),
                    "recommended_action": det.get("recommended_action")
                    or assessment.get("recommended_action"),
                    "repair_type": det.get("repair_type"),
                    "manual_review_required": bool(det.get("manual_review_required")),
                    "confidence": det.get("confidence"),
                    "ward_id": inspection.ward_id,
                    "ward_name": None,
                    "status": det.get("status") or inspection.status,
                    "location_name": inspection.location_name,
                    "address": inspection.location_name,
                    "timestamp": created or (
                        inspection.created_at.isoformat() if inspection.created_at else None
                    ),
                    "thumbnail_url": thumb,
                    "latitude": lat,
                    "longitude": lng,
                    "metric_depth_available": False,
                    "source": "offline_memory",
                },
            )
        )
        used_inspections.add(inspection.id)

    # Inspections without detections still appear (capture-before-analysis)
    if not severity and not priority:
        for inspection in inspections.values():
            if inspection.id in used_inspections:
                continue
            if ward_id is not None and inspection.ward_id != ward_id:
                continue
            lat = _safe_float(inspection.location_latitude)
            lng = _safe_float(inspection.location_longitude)
            if lat is None or lng is None:
                continue
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                continue
            media = inspection.media_items[0] if inspection.media_items else None
            thumb = None
            if media is not None:
                thumb = media.thumbnail_path or media.cdn_url or media.file_path
            features.append(
                _point_feature(
                    lat,
                    lng,
                    {
                        "feature_type": "inspection",
                        "detection_id": None,
                        "inspection_id": inspection.id,
                        "inspection_code": inspection.inspection_code,
                        "damage_type": None,
                        "severity": None,
                        "priority": None,
                        "ward_id": inspection.ward_id,
                        "status": inspection.status,
                        "location_name": inspection.location_name,
                        "address": inspection.location_name,
                        "timestamp": (
                            inspection.inspection_date.isoformat()
                            if inspection.inspection_date
                            else None
                        ),
                        "thumbnail_url": thumb,
                        "latitude": lat,
                        "longitude": lng,
                        "source": "offline_memory",
                    },
                )
            )

    return feature_collection(features)


def filter_geojson_by_radius(
    collection: dict,
    latitude: float,
    longitude: float,
    radius_meters: float,
) -> dict:
    """Haversine filter over a FeatureCollection of Point features."""
    kept: list[dict] = []
    for feature in collection.get("features") or []:
        geom = feature.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lng, lat = float(coords[0]), float(coords[1])
        distance = haversine_meters(latitude, longitude, lat, lng)
        if distance <= radius_meters:
            props = dict(feature.get("properties") or {})
            props["distance_meters"] = round(distance, 2)
            props["radius_meters"] = radius_meters
            kept.append(
                {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": props,
                }
            )
    return feature_collection(kept)


def map_detections_payload(
    *,
    municipality_id: int | None = None,
    severity: list[str] | None = None,
    ward_id: int | None = None,
    priority: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    prefer_offline: bool = False,
) -> dict:
    """Prefer PostGIS when available; fall back to offline memory."""
    from app.services.offline_store import offline_mode_enabled

    if prefer_offline or offline_mode_enabled():
        payload = offline_map_detections_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )
        payload["mode"] = "offline"
        return payload

    try:
        from app.utils.geo_utils import detections_as_geojson

        payload = detections_as_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )
        # Enrich offline memory on top when both exist
        offline = offline_map_detections_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )
        if offline.get("features"):
            payload = feature_collection(
                list(payload.get("features") or []) + list(offline.get("features") or [])
            )
        payload["mode"] = "postgis+offline"
        return payload
    except Exception:
        payload = offline_map_detections_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )
        payload["mode"] = "offline_fallback"
        return payload


def map_radius_payload(
    *,
    latitude: float,
    longitude: float,
    radius_meters: float,
    municipality_id: int | None = None,
    severity: list[str] | None = None,
    priority: list[str] | None = None,
    prefer_offline: bool = False,
) -> dict:
    from app.services.offline_store import offline_mode_enabled

    if not prefer_offline and not offline_mode_enabled():
        try:
            from app.utils.geo_utils import radius_search

            payload = radius_search(
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                municipality_id=municipality_id,
            )
            payload["mode"] = "postgis"
            return payload
        except Exception:
            pass

    base = offline_map_detections_geojson(
        municipality_id=municipality_id,
        severity=severity,
        priority=priority,
    )
    payload = filter_geojson_by_radius(base, latitude, longitude, radius_meters)
    payload["mode"] = "haversine"
    return payload
