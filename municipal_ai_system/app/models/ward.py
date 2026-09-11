from geoalchemy2 import Geometry

from app.extensions import db
from app.models.municipality import Municipality


class Ward(db.Model):
    __tablename__ = "wards"

    id = db.Column("ward_id", db.Integer, primary_key=True)

    municipality_id = db.Column(
        db.Integer,
        db.ForeignKey("municipalities.municipality_id"),
        nullable=False
    )

    name = db.Column(
        "ward_name",
        db.String(255),
        nullable=False
    )

    code = db.Column(
        "ward_code",
        db.String(100),
        nullable=True
    )

    ward_boundary = db.Column(
        Geometry("POLYGON", srid=4326),
        nullable=True
    )

    administrator_name = db.Column(db.String(255), nullable=True)
    administrator_email = db.Column(db.String(255), nullable=True)
    administrator_phone = db.Column(db.String(50), nullable=True)

    area_sq_km = db.Column(db.Numeric, nullable=True)
    population = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    municipality = db.relationship(
        Municipality,
        backref=db.backref("wards", lazy=True)
    )

    def __repr__(self) -> str:
        return f"<Ward {self.name}>"