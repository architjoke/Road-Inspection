from app.extensions import db


class Municipality(db.Model):
    __tablename__ = "municipalities"

    id = db.Column("municipality_id", db.Integer, primary_key=True)

    name = db.Column(
        "municipality_name",
        db.String(255),
        nullable=False
    )

    code = db.Column(
        "municipality_code",
        db.String(100),
        nullable=True
    )

    region = db.Column(db.String(255), nullable=True)
    state = db.Column(db.String(255), nullable=True)
    country = db.Column(db.String(100), nullable=True)

    contact_email = db.Column(db.String(255), nullable=True)
    contact_phone = db.Column(db.String(50), nullable=True)

    address = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Municipality {self.name}>"