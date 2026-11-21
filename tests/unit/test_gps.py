"""GPS service offline tests."""

from app.services.gps_service import (
    convert_dms_to_decimal,
    validate_gps_bounds,
)


def test_convert_dms_to_decimal_north_east():
    lat = convert_dms_to_decimal(16, 42, 18.0, "N")
    lng = convert_dms_to_decimal(74, 14, 35.16, "E")
    assert 16.7 < lat < 16.71
    assert 74.24 < lng < 74.25


def test_convert_dms_south_west_negative():
    assert convert_dms_to_decimal(10, 0, 0, "S") < 0
    assert convert_dms_to_decimal(70, 0, 0, "W") < 0


def test_validate_gps_bounds():
    assert validate_gps_bounds(16.705, 74.243) is True
    assert validate_gps_bounds(0, 0) is False
    assert validate_gps_bounds(100, 0) is False
