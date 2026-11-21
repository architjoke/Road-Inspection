from datetime import datetime

from app.models.inspection import Inspection


def generate_inspection_code() -> str:
    current_year = datetime.now().year
    latest_inspection = (
        Inspection.query
        .order_by(Inspection.id.desc())
        .first()
    )

    next_number = 1

    if latest_inspection is not None:
        next_number = latest_inspection.id + 1

    return f"INS-{current_year}-{next_number:06d}"