from datetime import datetime

import bcrypt
from flask_login import UserMixin

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    # ============================================================
    # PRIMARY KEY
    # ============================================================

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # ============================================================
    # MUNICIPALITY
    # ============================================================

    municipality_id = db.Column(
        db.Integer,
        db.ForeignKey("municipalities.municipality_id"),
        nullable=True
    )

    # ============================================================
    # ROLE
    # ============================================================

    role_id = db.Column(
        db.Integer,
        db.ForeignKey("roles.role_id"),
        nullable=False
    )

    # ============================================================
    # USER INFORMATION
    # ============================================================

    username = db.Column(
        db.String(100),
        unique=True,
        nullable=False,
        index=True
    )

    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    full_name = db.Column(
        db.String(200),
        nullable=False
    )

    phone = db.Column(
        db.String(20),
        nullable=True
    )

    # ============================================================
    # ACCOUNT STATUS
    # ============================================================

    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=True
    )

    is_verified = db.Column(
        db.Boolean,
        default=False,
        nullable=True
    )

    # ============================================================
    # LOGIN TRACKING
    # ============================================================

    failed_login_attempts = db.Column(
        db.Integer,
        default=0,
        nullable=True
    )

    last_login_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # ============================================================
    # TIMESTAMPS
    # ============================================================

    created_at = db.Column(
        db.DateTime,
        nullable=True
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # ============================================================
    # RELATIONSHIPS
    # ============================================================

    role = db.relationship(
        "Role",
        backref=db.backref("users", lazy=True)
    )

    municipality = db.relationship(
        "Municipality",
        backref=db.backref("users", lazy=True)
    )

    # ============================================================
    # PASSWORD VERIFICATION
    # ============================================================

    def check_password(self, password: str) -> bool:
        """
        Verify the supplied password against the existing
        bcrypt password hash stored in PostgreSQL.
        """

        if not password or not self.password_hash:
            return False

        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                self.password_hash.encode("utf-8")
            )

        except (ValueError, TypeError):
            return False

    # ============================================================
    # ROLE NAME
    # ============================================================

    @property
    def role_name(self) -> str:
        """
        Return the exact normalized role name used by the application.

        Examples:
            SUPER_ADMIN      -> super_admin
            MUNICIPAL_ADMIN  -> municipal_admin
            FIELD_OFFICER    -> field_officer
            FIELD_WORKER     -> field_worker
            CONTRACTOR       -> contractor
            VIEWER           -> viewer
        """

        if not self.role:
            return ""

        return (self.role.name or "").strip().lower()

    # ============================================================
    # ROLE CHECKING
    # ============================================================

    def has_role(self, *allowed_roles: str) -> bool:
        """
        Check whether the user has one of the specified roles.

        Role comparison is case-insensitive.
        """

        current_role = self.role_name

        if not current_role:
            return False

        normalized_roles = {
            role.strip().lower()
            for role in allowed_roles
        }

        return current_role in normalized_roles

    # ============================================================
    # ROLE-SPECIFIC CHECKS
    # ============================================================

    def is_super_admin(self) -> bool:
        return self.has_role("super_admin")

    def is_municipal_admin(self) -> bool:
        return self.has_role("municipal_admin")

    def is_admin(self) -> bool:
        """
        Compatibility method used by the existing Phase 3
        templates/routes.

        This refers specifically to municipal_admin.
        """
        return self.has_role("municipal_admin")

    def is_field_officer(self) -> bool:
        return self.has_role("field_officer")

    def is_field_worker(self) -> bool:
        return self.has_role("field_worker")

    def is_contractor(self) -> bool:
        return self.has_role("contractor")

    def is_viewer(self) -> bool:
        return self.has_role("viewer")

    # ============================================================
    # LOGIN SUCCESS
    # ============================================================

    def mark_login_success(self) -> None:
        """
        Reset failed login attempts and record the successful
        login timestamp.
        """

        self.failed_login_attempts = 0
        self.last_login_at = datetime.utcnow()

    # ============================================================
    # FAILED LOGIN
    # ============================================================

    def register_failed_login(self) -> None:
        """
        Increment the failed login counter.
        """

        self.failed_login_attempts = (
            self.failed_login_attempts or 0
        ) + 1

    # ============================================================
    # REPRESENTATION
    # ============================================================

    def __repr__(self) -> str:
        return f"<User {self.username}>"