"""Location label helpers: street, landmark, ward, city, DMS."""

from __future__ import annotations

from typing import Any


def decimal_to_dms(value: float, *, lat: bool) -> str:
    hemi = ("N" if value >= 0 else "S") if lat else ("E" if value >= 0 else "W")
    abs_val = abs(float(value))
    degrees = int(abs_val)
    minutes_full = (abs_val - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60
    return f"{degrees}°{minutes:02d}'{seconds:05.2f}\"{hemi}"


def format_location_line(parts: dict[str, Any] | None, *, lat=None, lng=None) -> str:
    data = parts or {}
    street = data.get("street_name") or data.get("road") or data.get("address")
    landmark = data.get("landmark") or data.get("suburb") or data.get("neighbourhood")
    ward = data.get("ward") or data.get("ward_name")
    city = data.get("city") or data.get("town")

    chunks: list[str] = []
    if street:
        chunks.append(str(street))
    if landmark and str(landmark).lower() not in (street or "").lower():
        chunks.append(f"near {landmark}")
    if ward:
        label = str(ward)
        if "ward" not in label.lower():
            label = f"{label} Ward"
        chunks.append(label)
    if city:
        chunks.append(str(city))
    if chunks:
        return "Location: " + ", ".join(chunks)
    if lat is not None and lng is not None:
        return f"Location: {float(lat):.6f}, {float(lng):.6f}"
    return "Location: not recorded"


def format_capture_location(parts: dict[str, Any] | None) -> str:
    """Street, landmark, ward, city — or Unknown Location."""
    data = parts or {}
    street = data.get("street_name") or data.get("road")
    landmark = data.get("landmark") or data.get("suburb") or data.get("neighbourhood")
    ward = data.get("ward") or data.get("ward_name")
    city = data.get("city") or data.get("town")
    chunks: list[str] = []
    if street:
        chunks.append(str(street))
    if landmark and str(landmark).lower() not in str(street or "").lower():
        chunks.append(f"near {landmark}")
    if ward:
        label = str(ward)
        if "ward" not in label.lower():
            label = f"{label} Ward"
        chunks.append(label)
    if city:
        chunks.append(str(city))
    return ", ".join(chunks) if chunks else "Unknown Location"
