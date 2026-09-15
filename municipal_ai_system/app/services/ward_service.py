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


def list_wards_for_municipality(municipality_id: int | None = None) -> list[Ward]:
    query = Ward.query.order_by(Ward.name.asc())
    if municipality_id is not None:
        query = query.filter(Ward.municipality_id == municipality_id)
    return query.all()
