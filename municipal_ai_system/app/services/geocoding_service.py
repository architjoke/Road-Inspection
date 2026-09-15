"""Reverse geocoding via OpenStreetMap Nominatim (no Google/Mapbox)."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "MunicipalRoadInspectionSystem/1.0 (municipal-ai; contact=local-dev)"

# Soft client-side throttle for Nominatim usage policy (~1 req/sec).
_last_request_at = 0.0
_GEOCODE_CACHE: dict[tuple[float, float], dict[str, Any]] = {}
_GEOCODE_CACHE_MAX = 256


def _cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (round(float(latitude), 5), round(float(longitude), 5))


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    _last_request_at = time.monotonic()


def _compose_street_address(payload: dict[str, Any]) -> str:
    """Build a readable full street address from Nominatim address parts."""
    addr = payload.get("address") or {}

    road = (
        addr.get("road")
        or addr.get("pedestrian")
        or addr.get("path")
        or addr.get("residential")
        or addr.get("highway")
        or addr.get("neighbourhood")
    )
    house = addr.get("house_number")
    suburb = addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter")
    village = addr.get("village") or addr.get("town") or addr.get("city_district")
    city = addr.get("city") or addr.get("town") or addr.get("municipality") or addr.get("county")
    state = addr.get("state")
    postcode = addr.get("postcode")
    country = addr.get("country")

    line_parts: list[str] = []
    if house and road:
        line_parts.append(f"{house} {road}")
    elif road:
        line_parts.append(road)
    elif suburb:
        line_parts.append(suburb)

    for part in (suburb, village, city, state, postcode, country):
        if part and part not in line_parts:
            # Avoid duplicating suburb if already used as primary
            if part == suburb and line_parts and line_parts[0] == suburb:
                continue
            line_parts.append(part)

    if line_parts:
        return ", ".join(line_parts)

    display = (payload.get("display_name") or "").strip()
    return display


def reverse_geocode(
    latitude: float,
    longitude: float,
    *,
    zoom: int = 18,
    language: str = "en",
) -> dict[str, Any] | None:
    """
    Convert GPS coordinates to a full street address.

    Returns:
        {
          'display_name': '...',
          'address': '...',          # composed street address
          'road': '...',
          'city': '...',
          'state': '...',
          'postcode': '...',
          'country': '...',
          'raw': {...}
        }
    or None on failure.
    """
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None

    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        return None

    key = _cache_key(lat, lng)
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        return cached

    params = urlencode(
        {
            "lat": f"{lat:.8f}",
            "lon": f"{lng:.8f}",
            "format": "jsonv2",
            "addressdetails": 1,
            "zoom": zoom,
            "accept-language": language,
        }
    )
    request = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        _throttle()
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Reverse geocode failed for (%s, %s): %s",
            latitude,
            longitude,
            exc,
        )
        return None

    if not isinstance(payload, dict) or payload.get("error"):
        logger.warning("Nominatim returned no address for (%s, %s)", latitude, longitude)
        return None

    addr = payload.get("address") or {}
    composed = _compose_street_address(payload)
    display = (payload.get("display_name") or composed or "").strip()

    if not display:
        return None

    landmark = (
        addr.get("attraction")
        or addr.get("building")
        or addr.get("amenity")
        or addr.get("shop")
        or addr.get("tourism")
        or addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("quarter")
    )
    ward = (
        addr.get("city_district")
        or addr.get("district")
        or addr.get("suburb")
        or addr.get("county")
    )
    result = {
        "display_name": display,
        "address": composed or display,
        "road": addr.get("road") or addr.get("pedestrian") or addr.get("residential"),
        "street_name": addr.get("road") or addr.get("pedestrian") or addr.get("residential"),
        "landmark": landmark,
        "ward": ward,
        "suburb": addr.get("suburb") or addr.get("neighbourhood"),
        "city": addr.get("city") or addr.get("town") or addr.get("municipality"),
        "state": addr.get("state"),
        "postcode": addr.get("postcode"),
        "country": addr.get("country"),
        "raw": payload,
    }
    if len(_GEOCODE_CACHE) >= _GEOCODE_CACHE_MAX:
        _GEOCODE_CACHE.pop(next(iter(_GEOCODE_CACHE)))
    _GEOCODE_CACHE[key] = result
    return result


def resolve_location_label(
    latitude: float,
    longitude: float,
    fallback: str | None = None,
) -> str:
    """
    Prefer reverse-geocoded full address; fall back to provided label or coords.
    """
    result = reverse_geocode(latitude, longitude)
    if result and result.get("address"):
        return result["address"]

    if fallback and fallback.strip():
        return fallback.strip()

    return f"{float(latitude):.6f}, {float(longitude):.6f}"
