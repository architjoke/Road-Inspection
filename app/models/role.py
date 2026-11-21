from app.extensions import db


class Role(db.Model):
    __tablename__ = "roles"

    # Maps Python attribute `id` to PostgreSQL column `role_id`.
    id = db.Column("role_id", db.Integer, primary_key=True)

    # Maps Python attribute `name` to PostgreSQL column `role_name`.
    name = db.Column(
        "role_name",
        db.String(50),
        nullable=False,
        unique=True
    )

    description = db.Column(db.Text, nullable=True)
    permissions = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Role {self.name}>"