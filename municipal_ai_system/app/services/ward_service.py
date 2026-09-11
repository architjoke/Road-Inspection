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
    point = WKTElement(f"POINT({longitude} {latitude})", srid=4326)

    try:
        filters = [
            Ward.ward_boundary.isnot(None),
            Ward.ward_boundary.ST_Contains(point),
        ]
        if municipality_id is not None:
            filters.append(Ward.municipality_id == municipality_id)

        return Ward.query.filter(and_(*filters)).first()
    except Exception:
        logger.exception(
            "Ward lookup failed for lat=%s lng=%s", latitude, longitude
        )
        return None


def list_wards_for_municipality(municipality_id: int | None = None) -> list[Ward]:
    query = Ward.query.order_by(Ward.name.asc())
    if municipality_id is not None:
        query = query.filter(Ward.municipality_id == municipality_id)
    return query.all()
