"""GeoJSON helpers for Leaflet map endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2.functions import ST_AsGeoJSON
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.types import Float

from app.extensions import db
from app.models import get_model
from app.models.inspection import Inspection
from app.models.ward import Ward
from app.utils.status_constants import FALSE_POSITIVE


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _feature(geometry_json: str | dict | None, properties: dict) -> dict | None:
    if not geometry_json:
        return None

    geometry = (
        json.loads(geometry_json)
        if isinstance(geometry_json, str)
        else geometry_json
    )

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {k: _json_safe(v) for k, v in properties.items()},
    }


def feature_collection(features: list[dict]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [f for f in features if f],
    }


def _detection_column_map(table) -> dict[str, str]:
    """Map logical names to actual reflected column names."""
    names = {c.name for c in table.columns}
    aliases = {
        "id": ["detection_id", "id"],
        "inspection_id": ["inspection_id"],
        "damage_type": [
            "damage_type",
            "defect_type",
            "defect_class",
            "class_name",
            "detection_class",
        ],
        "severity": ["severity", "severity_level", "severity_label"],
        "priority": ["priority", "priority_level"],
        "status": ["status", "review_status", "detection_status"],
        "geometry": [
            "location_geometry",
            "gps_location",
            "geom",
            "geometry",
        ],
        "created_at": ["created_at", "detected_at", "timestamp"],
        "confidence": ["confidence_score", "confidence", "score"],
    }

    resolved: dict[str, str] = {}
    for logical, candidates in aliases.items():
        for candidate in candidates:
            if candidate in names:
                resolved[logical] = candidate
                break
    return resolved


def inspections_as_geojson(
    municipality_id: int | None = None,
    severity: list[str] | None = None,
    ward_id: int | None = None,
    priority: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Build GeoJSON from inspections with GPS (works before AI detections exist)."""
    query = Inspection.query.filter(Inspection.location_geometry.isnot(None))

    if municipality_id is not None:
        query = query.filter(Inspection.municipality_id == municipality_id)
    if ward_id is not None:
        query = query.filter(Inspection.ward_id == ward_id)
    if date_from is not None:
        query = query.filter(Inspection.inspection_date >= date_from)
    if date_to is not None:
        query = query.filter(Inspection.inspection_date <= date_to)

    # severity/priority filters apply once detections exist; ignore for inspection markers
    _ = severity, priority

    rows = query.order_by(Inspection.id.desc()).limit(5000).all()
    features: list[dict] = []

    for inspection in rows:
        geojson = db.session.scalar(
            select(ST_AsGeoJSON(Inspection.location_geometry)).where(
                Inspection.id == inspection.id
            )
        )

        ward_name = None
        if inspection.ward_id:
            ward = db.session.get(Ward, inspection.ward_id)
            ward_name = ward.name if ward else None

        feature = _feature(
            geojson,
            {
                "feature_type": "inspection",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "damage_type": None,
                "severity": None,
                "priority": None,
                "ward_id": inspection.ward_id,
                "ward_name": ward_name,
                "status": inspection.status,
                "location_name": inspection.location_name,
                "timestamp": inspection.inspection_date,
            },
        )
        if feature:
            features.append(feature)

    return feature_collection(features)


def detections_as_geojson(
    municipality_id: int | None = None,
    severity: list[str] | None = None,
    ward_id: int | None = None,
    priority: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """
    Build GeoJSON from detections when the reflected table is available.
    Falls back to inspections GeoJSON when detections have no usable geometry.
    """
    Detection = get_model("detections")
    if Detection is None or not hasattr(Detection, "__table__"):
        return inspections_as_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )

    cols = _detection_column_map(Detection.__table__)
    geom_col_name = cols.get("geometry")
    if not geom_col_name:
        return inspections_as_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )

    geom_col = Detection.__table__.c[geom_col_name]
    id_col = Detection.__table__.c[cols["id"]] if "id" in cols else None
    inspection_col = (
        Detection.__table__.c[cols["inspection_id"]]
        if "inspection_id" in cols
        else None
    )
    damage_col = (
        Detection.__table__.c[cols["damage_type"]] if "damage_type" in cols else None
    )
    severity_col = (
        Detection.__table__.c[cols["severity"]] if "severity" in cols else None
    )
    priority_col = (
        Detection.__table__.c[cols["priority"]] if "priority" in cols else None
    )
    status_col = Detection.__table__.c[cols["status"]] if "status" in cols else None
    created_col = (
        Detection.__table__.c[cols["created_at"]] if "created_at" in cols else None
    )

    stmt = (
        select(
            id_col.label("detection_id") if id_col is not None else geom_col,
            inspection_col.label("inspection_id")
            if inspection_col is not None
            else cast(None, Float),
            damage_col.label("damage_type") if damage_col is not None else cast(None, Float),
            severity_col.label("severity") if severity_col is not None else cast(None, Float),
            priority_col.label("priority") if priority_col is not None else cast(None, Float),
            status_col.label("status") if status_col is not None else cast(None, Float),
            created_col.label("created_at") if created_col is not None else cast(None, Float),
            ST_AsGeoJSON(geom_col).label("geojson"),
            Inspection.inspection_code,
            Inspection.ward_id,
            Ward.name.label("ward_name"),
        )
        .select_from(Detection.__table__)
        .join(
            Inspection,
            Inspection.id == inspection_col,
            isouter=True,
        )
        .join(Ward, Ward.id == Inspection.ward_id, isouter=True)
        .where(geom_col.isnot(None))
    )

    if status_col is not None:
        stmt = stmt.where(
            or_(
                status_col.is_(None),
                func.lower(cast(status_col, String)) != FALSE_POSITIVE,
            )
        )

    if municipality_id is not None:
        stmt = stmt.where(Inspection.municipality_id == municipality_id)
    if ward_id is not None:
        stmt = stmt.where(Inspection.ward_id == ward_id)
    if severity and severity_col is not None:
        normalized = [s.upper() for s in severity]
        stmt = stmt.where(func.upper(cast(severity_col, String)).in_(normalized))
    if priority and priority_col is not None:
        stmt = stmt.where(cast(priority_col, String).in_([str(p) for p in priority]))
    if date_from is not None and created_col is not None:
        stmt = stmt.where(created_col >= date_from)
    if date_to is not None and created_col is not None:
        stmt = stmt.where(created_col <= date_to)

    stmt = stmt.limit(5000)

    try:
        rows = db.session.execute(stmt).mappings().all()
    except Exception:
        # Reflected schema mismatch — still serve inspection markers.
        return inspections_as_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )

    if not rows:
        return inspections_as_geojson(
            municipality_id=municipality_id,
            severity=severity,
            ward_id=ward_id,
            priority=priority,
            date_from=date_from,
            date_to=date_to,
        )

    features = []
    for row in rows:
        feature = _feature(
            row.get("geojson"),
            {
                "feature_type": "detection",
                "detection_id": row.get("detection_id"),
                "inspection_id": row.get("inspection_id"),
                "inspection_code": row.get("inspection_code"),
                "damage_type": row.get("damage_type"),
                "severity": row.get("severity"),
                "priority": row.get("priority"),
                "ward_id": row.get("ward_id"),
                "ward_name": row.get("ward_name"),
                "status": row.get("status"),
                "timestamp": row.get("created_at"),
            },
        )
        if feature:
            features.append(feature)

    return feature_collection(features)


def radius_search(
    latitude: float,
    longitude: float,
    radius_meters: float,
    municipality_id: int | None = None,
) -> dict:
    """Return inspections within radius_meters of a point (PostGIS geography)."""
    distance_clause = text_dwithin_meters(
        "inspections.location_geometry", longitude, latitude, radius_meters
    )

    query = Inspection.query.filter(
        Inspection.location_geometry.isnot(None),
        distance_clause,
    )

    if municipality_id is not None:
        query = query.filter(Inspection.municipality_id == municipality_id)

    rows = query.order_by(Inspection.id.desc()).limit(1000).all()
    features = []

    for inspection in rows:
        geojson = db.session.scalar(
            select(ST_AsGeoJSON(Inspection.location_geometry)).where(
                Inspection.id == inspection.id
            )
        )
        ward_name = None
        if inspection.ward_id:
            ward = db.session.get(Ward, inspection.ward_id)
            ward_name = ward.name if ward else None

        feature = _feature(
            geojson,
            {
                "feature_type": "inspection",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "damage_type": None,
                "severity": None,
                "priority": None,
                "ward_id": inspection.ward_id,
                "ward_name": ward_name,
                "status": inspection.status,
                "location_name": inspection.location_name,
                "timestamp": inspection.inspection_date,
                "radius_meters": radius_meters,
            },
        )
        if feature:
            features.append(feature)

    return feature_collection(features)


def text_dwithin_meters(geometry_sql: str, lng: float, lat: float, meters: float):
    from sqlalchemy import text

    return text(
        f"ST_DWithin("
        f"CAST({geometry_sql} AS geography), "
        f"CAST(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) AS geography), "
        f":radius"
        f")"
    ).bindparams(lng=lng, lat=lat, radius=meters)


def wards_boundaries_geojson(municipality_id: int | None = None) -> dict:
    query = Ward.query.filter(Ward.ward_boundary.isnot(None))
    if municipality_id is not None:
        query = query.filter(Ward.municipality_id == municipality_id)

    wards = query.order_by(Ward.name.asc()).all()
    features = []

    for ward in wards:
        geojson = db.session.scalar(
            select(ST_AsGeoJSON(Ward.ward_boundary)).where(Ward.id == ward.id)
        )
        feature = _feature(
            geojson,
            {
                "ward_id": ward.id,
                "ward_name": ward.name,
                "ward_code": ward.code,
                "municipality_id": ward.municipality_id,
            },
        )
        if feature:
            features.append(feature)

    return feature_collection(features)
