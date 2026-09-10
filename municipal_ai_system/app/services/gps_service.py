from decimal import Decimal, InvalidOperation


def parse_coordinate(value: str, coordinate_name: str) -> Decimal:
    try:
        coordinate = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(
            f"{coordinate_name} must be a valid number."
        ) from error

    return coordinate


def validate_latitude(latitude: Decimal) -> Decimal:
    if latitude < Decimal("-90") or latitude > Decimal("90"):
        raise ValueError("Latitude must be between -90 and 90.")

    return latitude


def validate_longitude(longitude: Decimal) -> Decimal:
    if longitude < Decimal("-180") or longitude > Decimal("180"):
        raise ValueError("Longitude must be between -180 and 180.")

    return longitude


def parse_accuracy(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None

    try:
        accuracy = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(
            "GPS accuracy must be a valid number."
        ) from error

    if accuracy < Decimal("0"):
        raise ValueError("GPS accuracy cannot be negative.")

    return accuracy


def get_browser_gps(
    latitude_value: str,
    longitude_value: str,
    accuracy_value: str | None
) -> tuple[Decimal, Decimal, Decimal | None]:
    latitude = validate_latitude(
        parse_coordinate(latitude_value, "Latitude")
    )

    longitude = validate_longitude(
        parse_coordinate(longitude_value, "Longitude")
    )

    accuracy = parse_accuracy(accuracy_value)

    return latitude, longitude, accuracy