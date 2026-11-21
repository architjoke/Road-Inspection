from app.extensions import db


class InspectionMedia(db.Model):
    __tablename__ = "inspection_media"

    id = db.Column("media_id", db.Integer, primary_key=True)

    inspection_id = db.Column(
        db.Integer,
        db.ForeignKey("inspections.inspection_id"),
        nullable=False
    )

    media_type = db.Column(db.String(30), nullable=False)

    file_path = db.Column(db.String(1000), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)

    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    mime_type = db.Column(db.String(150), nullable=True)

    resolution_width = db.Column(db.Integer, nullable=True)
    resolution_height = db.Column(db.Integer, nullable=True)

    duration_seconds = db.Column(db.Numeric, nullable=True)

    captured_at = db.Column(db.DateTime, nullable=False)

    gps_latitude = db.Column(db.Numeric(10, 8), nullable=True)
    gps_longitude = db.Column(db.Numeric(11, 8), nullable=True)
    gps_accuracy_meters = db.Column(db.Numeric, nullable=True)
    altitude_meters = db.Column(db.Numeric, nullable=True)

    device_info = db.Column(db.JSON, nullable=True)
    exif_data = db.Column(db.JSON, nullable=True)

    is_before_image = db.Column(
        db.Boolean,
        nullable=True,
        default=True
    )

    is_after_image = db.Column(
        db.Boolean,
        nullable=True,
        default=False
    )

    storage_bucket = db.Column(db.String(255), nullable=True)
    cdn_url = db.Column(db.Text, nullable=True)
    thumbnail_path = db.Column(db.String(1000), nullable=True)

    processing_status = db.Column(
        db.String(50),
        nullable=True,
        default="pending"
    )

    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<InspectionMedia {self.file_name}>"