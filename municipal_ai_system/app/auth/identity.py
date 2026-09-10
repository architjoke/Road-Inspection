import logging

from flask_login import UserMixin

from app.auth.constants import KIND_SUPER_ADMIN, KIND_USER, ROLE_ADMINISTRATOR, ROLE_CONTRACTOR, ROLE_FIELD_OFFICER, ROLE_FIELD_WORKER, ROLE_SUPER_ADMIN, ROLE_VIEWER, canonical_role, normalize_role
from app.extensions import db, login_manager
from app.models import get_model

logger = logging.getLogger(__name__)


class UserIdentity(UserMixin):
    def __init__(
        self,
        *,
        kind,
        pk,
        username,
        email,
        full_name,
        role_name,
        municipality_id=None,
        ward_id=None,
        role_id=None,
        is_active=True,
        municipality_ids=None,
        permissions=None,
    ):
        self.kind = kind
        self.pk = pk
        self.username = username
        self.email = email
        self.full_name = full_name
        self.role_name = role_name
        self.municipality_id = municipality_id
        self.ward_id = ward_id
        self.role_id = role_id
        self._is_active = bool(is_active) if is_active is not None else True
        self.municipality_ids = list(municipality_ids or [])
        self.permissions = permissions or {}

    @property
    def id(self):
        return self.pk

    @property
    def display_role_name(self):
        raw = (self.role_name or "No role").strip()
        return raw.replace("_", " ").title()

    @property
    def dashboard_role_key(self):
        canon = canonical_role(self.role_name)
        mapping = {
            ROLE_SUPER_ADMIN: "super_admin",
            ROLE_ADMINISTRATOR: "municipal_admin",
            ROLE_FIELD_OFFICER: "field_officer",
            ROLE_FIELD_WORKER: "field_worker",
            ROLE_CONTRACTOR: "contractor",
            ROLE_VIEWER: "viewer",
        }
        if canon in mapping:
            return mapping[canon]
        return (self.role_name or "").strip().lower().replace(" ", "_")

    def get_id(self):
        prefix = 'sa' if self.kind == KIND_SUPER_ADMIN else 'user'
        return f'{prefix}:{self.pk}'

    @property
    def is_active(self):
        return self._is_active

    @property
    def is_super_admin(self):
        if self.kind == KIND_SUPER_ADMIN:
            return True
        return canonical_role(self.role_name) == ROLE_SUPER_ADMIN

    def has_role(self, *role_names):
        allowed = {
            normalize_role(canonical_role(name) or name)
            for name in role_names
        }
        allowed.discard('')
        if self.is_super_admin:
            return normalize_role(ROLE_SUPER_ADMIN) in allowed
        actual = normalize_role(canonical_role(self.role_name) or self.role_name)
        return bool(actual) and actual in allowed

    def is_municipal_admin(self):
        return self.has_role(ROLE_ADMINISTRATOR, "municipal_admin")

    def is_admin(self):
        return self.is_municipal_admin()

    def is_field_officer(self):
        return self.has_role(ROLE_FIELD_OFFICER, "field_officer")

    def is_field_worker(self):
        return self.has_role(ROLE_FIELD_WORKER, "field_worker")

    def is_contractor(self):
        return self.has_role(ROLE_CONTRACTOR, "contractor")

    def is_viewer(self):
        return self.has_role(ROLE_VIEWER, "viewer")


def _role_for_user(user_row):
    Role = get_model('roles')
    if Role is None:
        return None, {}
    role = db.session.get(Role, user_row.role_id)
    if role is None:
        return None, {}
    role_name = getattr(role, "role_name", None) or getattr(role, "name", None)
    return role_name, role.permissions or {}


def identity_from_user(user_row):
    if user_row is None:
        return None
    role_name, permissions = _role_for_user(user_row)
    return UserIdentity(
        kind=KIND_USER,
        pk=getattr(user_row, "user_id", None) or getattr(user_row, "id", None),
        username=user_row.username,
        email=user_row.email,
        full_name=user_row.full_name,
        role_name=role_name or getattr(user_row, "role_name", None),
        municipality_id=getattr(user_row, "municipality_id", None),
        ward_id=getattr(user_row, "ward_id", None),
        role_id=getattr(user_row, "role_id", None),
        is_active=user_row.is_active,
        municipality_ids=[user_row.municipality_id] if getattr(user_row, "municipality_id", None) else [],
        permissions=permissions,
    )


def identity_from_super_admin(admin_row):
    if admin_row is None:
        return None

    Oversight = get_model('municipality_oversight')
    municipality_ids = []
    if Oversight is not None:
        rows = (
            db.session.query(Oversight)
            .filter(Oversight.super_admin_id == admin_row.super_admin_id)
            .all()
        )
        municipality_ids = [
            row.municipality_id
            for row in rows
            if row.is_active is not False
        ]

    return UserIdentity(
        kind=KIND_SUPER_ADMIN,
        pk=admin_row.super_admin_id,
        username=admin_row.username,
        email=admin_row.email,
        full_name=admin_row.full_name,
        role_name=ROLE_SUPER_ADMIN,
        is_active=admin_row.is_active,
        municipality_ids=municipality_ids,
    )


def load_identity(session_user_id):
    if not session_user_id or ':' not in session_user_id:
        return None

    kind, _, raw_pk = session_user_id.partition(':')
    try:
        pk = int(raw_pk)
    except (TypeError, ValueError):
        return None

    if kind == 'user':
        User = get_model('users')
        if User is None:
            return None
        return identity_from_user(db.session.get(User, pk))

    if kind == 'sa':
        SuperAdmin = get_model('super_admins')
        if SuperAdmin is None:
            return None
        return identity_from_super_admin(db.session.get(SuperAdmin, pk))

    return None


@login_manager.user_loader
def load_user(user_id):
    try:
        return load_identity(user_id)
    except Exception:
        logger.exception('Failed to load session user')
        return None
