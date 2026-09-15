"""User / municipality / ward directory. RDS when configured; memory otherwise."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from flask import current_app, has_app_context

from app.auth.passwords import hash_password
from app.extensions import db
from app.models import Municipality, Role, User, Ward, get_model
from app.services.offline_store import offline_mode_enabled


_lock = threading.RLock()
_users: dict[int, "DirectoryUser"] = {}
_municipalities: dict[int, "DirectoryMunicipality"] = {}
_wards: dict[int, "DirectoryWard"] = {}
_next_user = 1
_next_muni = 1
_next_ward = 1


@dataclass
class DirectoryUser:
    id: int
    username: str
    email: str
    full_name: str
    role_id: int | None = None
    role_name: str = ""
    municipality_id: int | None = None
    phone: str | None = None
    is_active: bool = True
    password_hash: str | None = None

    @property
    def display_role_name(self) -> str:
        raw = (self.role_name or "").replace("_", " ").strip()
        return raw.title() if raw else "—"

    @property
    def municipality(self):
        return None


@dataclass
class DirectoryMunicipality:
    id: int
    name: str
    code: str | None = None
    region: str | None = None
    state: str | None = None
    country: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: str | None = None


@dataclass
class DirectoryWard:
    id: int
    name: str
    municipality_id: int | None = None
    code: str | None = None
    administrator_name: str | None = None
    administrator_email: str | None = None
    administrator_phone: str | None = None
    population: int | None = None
    municipality: Any = field(default=None)


def _use_db() -> bool:
    if not has_app_context():
        return False
    if current_app.config.get("TESTING") or offline_mode_enabled():
        return False
    return bool(current_app.config.get("DATABASE_CONFIGURED"))


def clear_directory() -> None:
    global _next_user, _next_muni, _next_ward
    with _lock:
        _users.clear()
        _municipalities.clear()
        _wards.clear()
        _next_user = 1
        _next_muni = 1
        _next_ward = 1


def list_roles() -> list[Any]:
    if _use_db():
        Model = get_model("roles") or Role
        try:
            return db.session.query(Model).order_by(Model.name.asc()).all()
        except Exception:
            db.session.rollback()
            try:
                return db.session.query(Model).all()
            except Exception:
                db.session.rollback()
                return []
    return [
        DirectoryUser(id=1, username="", email="", full_name="", role_id=1, role_name="municipal_admin"),
    ]


def default_role_choices() -> list[tuple[int | str, str]]:
    if _use_db():
        rows = list_roles()
        choices = []
        for row in rows:
            pk = getattr(row, "id", None) or getattr(row, "role_id", None)
            name = getattr(row, "name", None) or getattr(row, "role_name", None) or str(pk)
            choices.append((pk, name))
        return choices
    return [
        ("municipal_admin", "Municipal Admin"),
        ("field_officer", "Field Officer"),
        ("field_worker", "Field Worker"),
        ("contractor", "Contractor"),
        ("viewer", "Viewer"),
    ]


def list_users(*, municipality_id: int | None = None) -> list[Any]:
    if _use_db():
        Model = get_model("users") or User
        query = db.session.query(Model)
        if municipality_id is not None and hasattr(Model, "municipality_id"):
            query = query.filter(Model.municipality_id == municipality_id)
        if hasattr(Model, "username"):
            query = query.order_by(Model.username.asc())
        try:
            return query.limit(300).all()
        except Exception:
            db.session.rollback()
            return []
    with _lock:
        rows = list(_users.values())
    if municipality_id is not None:
        rows = [u for u in rows if u.municipality_id == municipality_id]
    return rows


def get_user(user_id: int) -> Any | None:
    if _use_db():
        Model = get_model("users") or User
        try:
            return db.session.get(Model, user_id)
        except Exception:
            db.session.rollback()
            return None
    with _lock:
        return _users.get(int(user_id))


def create_user(
    *,
    username: str,
    email: str,
    full_name: str,
    password: str,
    role_id: int | str | None = None,
    municipality_id: int | None = None,
    phone: str | None = None,
    is_active: bool = True,
) -> tuple[Any | None, str | None]:
    username = (username or "").strip()
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    if not username or not email or not full_name or not password:
        return None, "Username, email, full name, and password are required."
    if len(password) < 8:
        return None, "Password must be at least 8 characters."

    if _use_db():
        Model = get_model("users") or User
        try:
            if db.session.query(Model).filter(Model.username == username).first():
                return None, "That username is already in use."
            if db.session.query(Model).filter(Model.email == email).first():
                return None, "That email is already in use."
            now = datetime.utcnow()
            rid = int(role_id) if role_id not in (None, "") else None
            row = Model(
                municipality_id=municipality_id,
                role_id=rid,
                username=username,
                email=email,
                password_hash=hash_password(password),
                full_name=full_name,
                phone=phone or None,
                is_active=is_active,
                is_verified=True,
                failed_login_attempts=0,
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
            db.session.commit()
            return row, None
        except Exception as exc:
            db.session.rollback()
            return None, f"Could not create user: {exc}"

    global _next_user
    with _lock:
        for existing in _users.values():
            if existing.username.lower() == username.lower() or existing.email.lower() == email:
                return None, "Username or email already exists."
        pk = _next_user
        _next_user += 1
        role_name = str(role_id or "field_worker")
        try:
            numeric_role = int(role_id) if role_id not in (None, "") else None
        except (TypeError, ValueError):
            numeric_role = None
            role_name = str(role_id or "field_worker")
        else:
            role_name = str(role_id)
        user = DirectoryUser(
            id=pk,
            username=username,
            email=email,
            full_name=full_name,
            role_id=numeric_role,
            role_name=role_name,
            municipality_id=municipality_id,
            phone=phone,
            is_active=is_active,
            password_hash=hash_password(password),
        )
        _users[pk] = user
        return user, None


def update_user(user_id: int, **fields) -> tuple[Any | None, str | None]:
    row = get_user(user_id)
    if row is None:
        return None, "User not found."
    password = fields.pop("password", None)
    if _use_db():
        try:
            for key in ("full_name", "email", "phone", "role_id", "municipality_id", "is_active"):
                if key in fields and fields[key] is not None:
                    if hasattr(row, key):
                        setattr(row, key, fields[key])
            if password:
                row.password_hash = hash_password(password)
            if hasattr(row, "updated_at"):
                row.updated_at = datetime.utcnow()
            db.session.commit()
            return row, None
        except Exception as exc:
            db.session.rollback()
            return None, str(exc)
    with _lock:
        for key, value in fields.items():
            if hasattr(row, key) and value is not None:
                setattr(row, key, value)
        if password:
            row.password_hash = hash_password(password)
        return row, None


def list_municipalities() -> list[Any]:
    if _use_db():
        Model = get_model("municipalities") or Municipality
        try:
            query = db.session.query(Model)
            if hasattr(Model, "name"):
                query = query.order_by(Model.name.asc())
            return query.limit(200).all()
        except Exception:
            db.session.rollback()
            return []
    with _lock:
        return list(_municipalities.values())


def get_municipality(municipality_id: int) -> Any | None:
    if _use_db():
        Model = get_model("municipalities") or Municipality
        try:
            return db.session.get(Model, municipality_id)
        except Exception:
            db.session.rollback()
            return None
    with _lock:
        return _municipalities.get(int(municipality_id))


def create_municipality(**fields) -> tuple[Any | None, str | None]:
    name = (fields.get("name") or "").strip()
    if not name:
        return None, "Municipality name is required."
    if _use_db():
        Model = get_model("municipalities") or Municipality
        try:
            now = datetime.utcnow()
            row = Model(
                name=name,
                code=fields.get("code") or None,
                region=fields.get("region") or None,
                state=fields.get("state") or None,
                country=fields.get("country") or None,
                contact_email=fields.get("contact_email") or None,
                contact_phone=fields.get("contact_phone") or None,
                address=fields.get("address") or None,
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
            db.session.commit()
            return row, None
        except Exception as exc:
            db.session.rollback()
            return None, str(exc)
    global _next_muni
    with _lock:
        pk = _next_muni
        _next_muni += 1
        row = DirectoryMunicipality(id=pk, name=name, **{
            k: fields.get(k) for k in (
                "code", "region", "state", "country",
                "contact_email", "contact_phone", "address",
            )
        })
        _municipalities[pk] = row
        return row, None


def list_directory_wards(municipality_id: int | None = None) -> list[Any]:
    if _use_db():
        from app.services.ward_service import list_wards_for_municipality

        try:
            return list_wards_for_municipality(municipality_id)
        except Exception:
            db.session.rollback()
            return []
    with _lock:
        rows = list(_wards.values())
    if municipality_id is not None:
        rows = [w for w in rows if w.municipality_id == municipality_id]
    return rows


def create_ward(**fields) -> tuple[Any | None, str | None]:
    name = (fields.get("name") or "").strip()
    if not name:
        return None, "Ward name is required."
    municipality_id = fields.get("municipality_id")
    try:
        municipality_id = int(municipality_id) if municipality_id not in (None, "") else None
    except (TypeError, ValueError):
        municipality_id = None
    if _use_db():
        Model = get_model("wards") or Ward
        try:
            now = datetime.utcnow()
            row = Model(
                name=name,
                municipality_id=municipality_id,
                code=fields.get("code") or None,
                administrator_name=fields.get("administrator_name") or None,
                administrator_email=fields.get("administrator_email") or None,
                administrator_phone=fields.get("administrator_phone") or None,
            )
            pop = fields.get("population")
            if pop not in (None, "") and hasattr(row, "population"):
                row.population = int(pop)
            if hasattr(row, "created_at"):
                row.created_at = now
            if hasattr(row, "updated_at"):
                row.updated_at = now
            db.session.add(row)
            db.session.commit()
            return row, None
        except Exception as exc:
            db.session.rollback()
            return None, str(exc)
    global _next_ward
    with _lock:
        pk = _next_ward
        _next_ward += 1
        pop = fields.get("population")
        try:
            population = int(pop) if pop not in (None, "") else None
        except (TypeError, ValueError):
            population = None
        muni = get_municipality(municipality_id) if municipality_id else None
        row = DirectoryWard(
            id=pk,
            name=name,
            municipality_id=municipality_id,
            code=fields.get("code") or None,
            administrator_name=fields.get("administrator_name") or None,
            administrator_email=fields.get("administrator_email") or None,
            administrator_phone=fields.get("administrator_phone") or None,
            population=population,
            municipality=muni,
        )
        _wards[pk] = row
        return row, None
