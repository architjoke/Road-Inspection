"""Ward spatial helpers using PostGIS."""

from __future__ import annotations

import logging

from geoalchemy2.elements import WKTElement
from sqlalchemy import and_

from app.models.ward import Ward

logger = logging.getLogger(__name__)


def find_ward_for_point(
    latitude: float,
    longitude: float,
    municipality_id: int | None = None,
) -> Ward | None:
    """Return the ward whose boundary contains the point, if any."""
    from app.services.offline_store import offline_mode_enabled

    if offline_mode_enabled():
        return None

    point = WKTElement(f"POINT({longitude} {latitude})", srid=4326)

    try:
        filters = [
            Ward.ward_boundary.isnot(None),
            Ward.ward_boundary.ST_Contains(point),
        ]
        if municipality_id is not None:
            filters.append(Ward.municipality_id == municipality_id)

        ward = Ward.query.filter(and_(*filters)).first()
        if ward is not None:
            return ward
    except Exception:
        logger.exception(
            "Ward GeoAlchemy lookup failed for lat=%s lng=%s", latitude, longitude
        )
        try:
            from sqlalchemy import text

            from app.extensions import db

            sql = text(
                """
                SELECT ward_id, ward_name
                FROM wards
                WHERE ward_boundary IS NOT NULL
                  AND ST_Contains(
                    ward_boundary,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)
                  )
                  AND (:municipality_id IS NULL OR municipality_id = :municipality_id)
                LIMIT 1
                """
            )
            row = db.session.execute(
                sql,
                {
                    "lat": float(latitude),
                    "lng": float(longitude),
                    "municipality_id": municipality_id,
                },
            ).first()
            if row is not None:
                return db.session.get(Ward, row[0])
        except Exception:
            logger.exception(
                "Ward ST_Contains fallback failed for lat=%s lng=%s",
                latitude,
                longitude,
            )
    logger.warning(
        "GPS point is outside all ward polygons lat=%s lng=%s municipality_id=%s",
        latitude,
        longitude,
        municipality_id,
    )
    return None


def assign_ward_from_gps(
    latitude: float,
    longitude: float,
    municipality_id: int | None = None,
) -> dict | None:
    """Return {ward_id, ward_name} or None when the point is unassigned."""
    ward = find_ward_for_point(latitude, longitude, municipality_id=municipality_id)
    if ward is None:
        return None
    return {
        "ward_id": getattr(ward, "id", None),
        "ward_name": getattr(ward, "name", None),
    }


def list_wards_for_municipality(municipality_id: int | None = None) -> list:
    """Return wards for a municipality; never raises (empty offline / DB down)."""
    from flask import current_app, has_app_context

    from app.extensions import db
    from app.services.offline_store import offline_mode_enabled

    if has_app_context():
        if offline_mode_enabled() or not current_app.config.get("DATABASE_CONFIGURED"):
            try:
                from app.services.directory_service import list_directory_wards

                return list_directory_wards(municipality_id=municipality_id)
            except Exception:
                return []
    try:
        query = Ward.query.order_by(Ward.name.asc())
        if municipality_id is not None:
            query = query.filter(Ward.municipality_id == municipality_id)
        return query.all()
    except Exception:
        logger.exception("list_wards_for_municipality failed")
        try:
            db.session.rollback()
        except Exception:
            pass
        return []


# --- Ward boundaries (Polygon, SRID 4326) ----------------------------------


def _ring_area(ring: list) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def parse_boundary_geojson(text: str) -> dict:
    """
    Accept a GeoJSON Polygon / MultiPolygon / Feature / FeatureCollection and
    return one Polygon geometry dict (the largest polygon when several exist).
    Raises ValueError with a user-facing message.
    """
    import json

    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Boundary is not valid GeoJSON.") from exc

    def geometries(obj):
        if not isinstance(obj, dict):
            return
        kind = obj.get("type")
        if kind == "FeatureCollection":
            for feature in obj.get("features") or []:
                yield from geometries(feature)
        elif kind == "Feature":
            yield from geometries(obj.get("geometry"))
        elif kind == "Polygon":
            yield obj.get("coordinates")
        elif kind == "MultiPolygon":
            for poly in obj.get("coordinates") or []:
                yield poly

    candidates = [c for c in geometries(data) if c]
    if not candidates:
        raise ValueError("Boundary must contain a Polygon or MultiPolygon.")
    polygon = max(candidates, key=lambda c: _ring_area(c[0]) if c and c[0] else 0.0)
    outer = [list(map(float, pt[:2])) for pt in polygon[0]]
    if outer and outer[0] != outer[-1]:
        outer.append(outer[0])
    if len(outer) < 4:
        raise ValueError("Boundary needs at least 3 distinct corner points.")
    for lng, lat in outer:
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError("Boundary coordinates must be [longitude, latitude] in degrees.")
    holes = []
    for ring in polygon[1:]:
        ring = [list(map(float, pt[:2])) for pt in ring]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) >= 4:
            holes.append(ring)
    return {"type": "Polygon", "coordinates": [outer, *holes]}


def get_ward_boundary_geojson(ward_id: int) -> str | None:
    from sqlalchemy import text

    from app.extensions import db

    try:
        return db.session.execute(
            text("SELECT ST_AsGeoJSON(ward_boundary) FROM wards WHERE ward_id = :id"),
            {"id": ward_id},
        ).scalar()
    except Exception:
        logger.exception("Could not read boundary for ward %s", ward_id)
        db.session.rollback()
        return None


def set_ward_boundary(ward_id: int, geojson_text: str) -> dict:
    """
    Store the ward polygon, compute its area, and assign the ward to existing
    inspections/detections inside it that have no ward yet.
    """
    import json

    from sqlalchemy import text

    from app.extensions import db
    from app.services.offline_store import offline_mode_enabled

    if offline_mode_enabled():
        raise ValueError("Ward boundaries need the database (offline mode is on).")

    geometry = json.dumps(parse_boundary_geojson(geojson_text))
    geom_sql = "ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326)"
    valid, reason = db.session.execute(
        text(f"SELECT ST_IsValid({geom_sql}), ST_IsValidReason({geom_sql})"), {"g": geometry}
    ).one()
    if not valid:
        raise ValueError(f"Boundary polygon is invalid ({reason}). Check for crossing edges.")

    try:
        area = db.session.execute(
            text(
                f"UPDATE wards SET ward_boundary = {geom_sql}, "
                f"area_sq_km = ROUND((ST_Area({geom_sql}::geography) / 1e6)::numeric, 4), "
                "updated_at = NOW() WHERE ward_id = :id RETURNING area_sq_km"
            ),
            {"g": geometry, "id": ward_id},
        ).scalar()
        inspections = db.session.execute(
            text(
                "UPDATE inspections i SET ward_id = w.ward_id, updated_at = NOW() "
                "FROM wards w WHERE w.ward_id = :id AND i.ward_id IS NULL "
                "AND i.municipality_id = w.municipality_id "
                "AND i.location_geometry IS NOT NULL "
                "AND ST_Contains(w.ward_boundary, ST_SetSRID(i.location_geometry, 4326))"
            ),
            {"id": ward_id},
        ).rowcount
        detections = db.session.execute(
            text(
                "UPDATE detections d SET ward_id = i.ward_id, updated_at = NOW() "
                "FROM inspections i WHERE d.inspection_id = i.inspection_id "
                "AND d.ward_id IS NULL AND i.ward_id = :id"
            ),
            {"id": ward_id},
        ).rowcount
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {
        "area_sq_km": float(area) if area is not None else None,
        "inspections_assigned": inspections,
        "detections_assigned": detections,
    }


def update_ward_fields(ward_id: int, **fields) -> Ward:
    from app.extensions import db

    ward = db.session.get(Ward, ward_id)
    if ward is None:
        raise ValueError("Ward not found.")
    name = (fields.get("name") or "").strip()
    if not name:
        raise ValueError("Ward name is required.")
    ward.name = name
    ward.code = (fields.get("code") or "").strip() or None
    ward.administrator_name = fields.get("administrator_name") or None
    ward.administrator_email = fields.get("administrator_email") or None
    ward.administrator_phone = fields.get("administrator_phone") or None
    population = fields.get("population")
    ward.population = int(population) if str(population or "").strip().isdigit() else None
    from datetime import datetime

    ward.updated_at = datetime.utcnow()
    db.session.commit()
    return ward
