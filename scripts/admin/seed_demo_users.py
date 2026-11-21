"""Seed six demo role accounts. Run: python scripts/admin/seed_demo_users.py"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import create_app
from app.auth.constants import canonical_role
from app.auth.passwords import hash_password
from app.extensions import db
from app.models import get_model

ACCOUNTS = (
    ("superadmin", "Admin@123", "Super Administrator", "super_admin", "Super Admin"),
    ("munadmin", "Admin@123", "Municipal Administrator", "municipal_admin", "Administrator"),
    ("officer", "Officer@123", "Field Officer", "field_officer", "Field Officer"),
    ("worker", "Worker@123", "Field Worker", "field_worker", "Field Worker"),
    ("contractor", "Contractor@123", "Road Contractor", "contractor", "Contractor"),
    ("viewer", "Viewer@123", "Public Viewer", "viewer", "Viewer"),
)


def _match_role(Role, wanted: tuple[str, str]):
    snake, display = wanted
    for row in db.session.query(Role).all():
        name = getattr(row, "name", None) or getattr(row, "role_name", None) or ""
        canon = canonical_role(name) or name
        key = str(name).strip().lower().replace(" ", "_")
        if key in {snake, display.lower().replace(" ", "_")} or str(canon).lower() in {
            snake.replace("_", " "),
            display.lower(),
        }:
            return row
        if snake in key or display.lower() in str(name).lower():
            return row
    return None


def main():
    app = create_app()
    with app.app_context():
        if not app.config.get("DATABASE_CONFIGURED"):
            print("DATABASE_URL is not configured. Set .env (project root) and retry.")
            return 1
        User = get_model("users")
        Role = get_model("roles")
        Municipality = get_model("municipalities")
        if User is None or Role is None:
            print("Could not reflect users/roles tables. Is DATABASE_URL reachable?")
            return 1
        municipality = None
        municipality_id = 1
        if Municipality is not None:
            try:
                municipality = db.session.query(Municipality).first()
                municipality_id = getattr(municipality, "id", None) if municipality else 1
            except Exception as exc:
                db.session.rollback()
                print(f"Database is not reachable from this machine: {exc}")
                return 1

        created = 0
        updated = 0
        SuperAdmin = get_model("super_admins")
        for username, password, full_name, snake, display in ACCOUNTS:
            if snake == "super_admin" and SuperAdmin is not None:
                row = (
                    db.session.query(SuperAdmin)
                    .filter(SuperAdmin.username == username)
                    .first()
                )
                pwd = hash_password(password)
                now = datetime.utcnow()
                if row is None:
                    kwargs = {
                        "username": username,
                        "email": f"{username}@municipality.local",
                        "password_hash": pwd,
                        "full_name": full_name,
                        "is_active": True,
                    }
                    if hasattr(SuperAdmin, "created_at"):
                        kwargs["created_at"] = now
                    if hasattr(SuperAdmin, "updated_at"):
                        kwargs["updated_at"] = now
                    try:
                        db.session.add(SuperAdmin(**kwargs))
                        created += 1
                        print(f"CREATED super_admins {username}")
                    except Exception as exc:
                        db.session.rollback()
                        print(f"SKIP super_admins {username}: {exc}")
                else:
                    row.password_hash = pwd
                    row.is_active = True
                    updated += 1
                    print(f"UPDATED super_admins password for {username}")

            role = _match_role(Role, (snake, display))
            if role is None:
                print(f"SKIP {username}: no role matching {snake}/{display}")
                continue
            role_id = getattr(role, "id", None) or getattr(role, "role_id", None)
            row = db.session.query(User).filter(User.username == username).first()
            now = datetime.utcnow()
            pwd = hash_password(password)
            if row is None:
                db.session.add(
                    User(
                        municipality_id=None if snake == "super_admin" else municipality_id,
                        role_id=role_id,
                        username=username,
                        email=f"{username}@municipality.local",
                        password_hash=pwd,
                        full_name=full_name,
                        is_active=True,
                        is_verified=True,
                        failed_login_attempts=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                created += 1
                print(f"CREATED {username}")
            else:
                row.password_hash = pwd
                row.role_id = role_id
                row.is_active = True
                if hasattr(row, "updated_at"):
                    row.updated_at = now
                updated += 1
                print(f"UPDATED password for {username}")
        db.session.commit()
        print(f"Done. created={created} updated={updated}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
