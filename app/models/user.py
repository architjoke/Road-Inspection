from datetime import datetime

import bcrypt
from flask_login import UserMixin

from app.extensions import db
from app.models.municipality import Municipality
from app.models.role import Role


class User(UserMixin, db.Model):
    """Mapped to live RDS public.users (PK column is `id`)."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    municipality_id = db.Column(
        db.Integer,
        db.ForeignKey("municipalities.municipality_id"),
        nullable=True,
    )

    role_id = db.Column(
        db.Integer,
        db.ForeignKey("roles.role_id"),
        nullable=False,
    )

    username = db.Column(db.String(100), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=True)

    failed_login_attempts = db.Column(db.Integer, default=0, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # NOTE: live RDS has no locked_until column — do not declare it.

    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    role = db.relationship(Role, backref=db.backref("users", lazy=True))
    municipality = db.relationship(
        Municipality, backref=db.backref("users", lazy=True)
    )

    @property
    def user_id(self):
        """Phase-3 / audit compatibility alias for the real PK `id`."""
        return self.id

    def check_password(self, password: str) -> bool:
        if not password or not self.password_hash:
            return False
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                self.password_hash.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False

    @property
    def role_name(self) -> str:
        if not self.role:
            return ""
        return (self.role.name or "").strip().lower()

    @property
    def display_role_name(self) -> str:
        raw = self.role_name or "No role"
        return raw.replace("_", " ").title()

    def has_role(self, *allowed_roles: str) -> bool:
        current_role = self.role_name
        if not current_role:
            return False
        normalized_roles = {role.strip().lower() for role in allowed_roles}
        return current_role in normalized_roles

    def is_super_admin(self) -> bool:
        return self.has_role("super_admin")

    def is_municipal_admin(self) -> bool:
        return self.has_role("municipal_admin", "administrator")

    def is_admin(self) -> bool:
        return self.is_municipal_admin()

    def is_field_officer(self) -> bool:
        return self.has_role("field_officer")

    def is_field_worker(self) -> bool:
        return self.has_role("field_worker")

    def is_contractor(self) -> bool:
        return self.has_role("contractor")

    def is_viewer(self) -> bool:
        return self.has_role("viewer")

    def mark_login_success(self) -> None:
        self.failed_login_attempts = 0
        self.last_login_at = datetime.utcnow()

    def register_failed_login(self) -> None:
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1

    def __repr__(self) -> str:
        return f"<User {self.username}>"
