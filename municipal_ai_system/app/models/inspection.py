from geoalchemy2 import Geometry

from app.extensions import db


class Inspection(db.Model):
    __tablename__ = "inspections"

    id = db.Column("inspection_id", db.Integer, primary_key=True)

    inspection_code = db.Column(
        db.String(100),
        nullable=False,
        unique=True
    )

    municipality_id = db.Column(
        db.Integer,
        db.ForeignKey("municipalities.municipality_id"),
        nullable=False
    )

    ward_id = db.Column(
        db.Integer,
        db.ForeignKey("wards.ward_id"),
        nullable=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.user_id"),
        nullable=False
    )

    inspection_date = db.Column(
        db.DateTime,
        nullable=False
    )

    location_name = db.Column(
        db.String(255),
        nullable=True
    )

    location_latitude = db.Column(
        db.Numeric(10, 8),
        nullable=True
    )

    location_longitude = db.Column(
        db.Numeric(11, 8),
        nullable=True
    )

    location_geometry = db.Column(
        Geometry("POINT", srid=4326),
        nullable=True
    )

    status = db.Column(
        db.String(50),
        nullable=True,
        default="pending_analysis"
    )

    inspection_type = db.Column(
        db.String(50),
        nullable=True,
        default="routine"
    )

    notes = db.Column(
        db.Text,
        nullable=True
    )

    weather_conditions = db.Column(
        db.String(100),
        nullable=True
    )

    lighting_conditions = db.Column(
        db.String(100),
        nullable=True
    )

    total_detections = db.Column(
        db.Integer,
        nullable=True,
        default=0
    )

    estimated_total_cost = db.Column(
        db.Numeric,
        nullable=True
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=True
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=True
    )

    media_items = db.relationship(
        "InspectionMedia",
        backref="inspection",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="InspectionMedia.id.desc()"
    )

    def __repr__(self) -> str:
        return f"<Inspection {self.inspection_code}>"