"""Optional seed for Phase 3 roles and one test Administrator.

Does not run automatically. Creates roles if missing. Creates a user only
when SEED_ADMIN_USERNAME, SEED_ADMIN_PASSWORD, SEED_ADMIN_EMAIL,
SEED_ADMIN_FULL_NAME, and SEED_MUNICIPALITY_ID are set.

Does not create municipalities. municipality_id must already exist.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.auth.constants import MUNICIPAL_ROLES, ROLE_ADMINISTRATOR, canonical_role
from app.auth.passwords import hash_password
from app.extensions import db
from app.models import get_model


ROLE_DESCRIPTIONS = {
    'Administrator': 'Review detections, assign work, and approve repairs.',
    'Field Worker': 'Capture and upload road inspection media.',
    'Field Officer': 'Verify detections on site.',
    'Contractor': 'Perform repairs and upload after photos.',
    'Viewer': 'Read-only access to inspections and reports.',
}


def _require_model(name):
    model = get_model(name)
    if model is None:
        raise RuntimeError(f'Table {name} is not reflected. Check DATABASE_URL and RDS access.')
    return model


def _role_already_present(Role, role_name):
    wanted = canonical_role(role_name) or role_name
    for row in db.session.query(Role).all():
        existing = canonical_role(row.role_name) or row.role_name
        if existing == wanted or (row.role_name or '').lower() == role_name.lower():
            return True
    return False


def ensure_roles():
    Role = _require_model('roles')
    created = []
    for role_name in MUNICIPAL_ROLES:
        if _role_already_present(Role, role_name):
            continue
        now = datetime.utcnow()
        db.session.add(Role(
            role_name=role_name,
            description=ROLE_DESCRIPTIONS[role_name],
            permissions={},
            created_at=now,
            updated_at=now,
        ))
        created.append(role_name)
    if created:
        db.session.commit()
    return created


def seed_admin_if_configured():
    username = os.getenv('SEED_ADMIN_USERNAME')
    password = os.getenv('SEED_ADMIN_PASSWORD')
    email = os.getenv('SEED_ADMIN_EMAIL')
    full_name = os.getenv('SEED_ADMIN_FULL_NAME')
    municipality_id = os.getenv('SEED_MUNICIPALITY_ID')

    required = {
        'SEED_ADMIN_USERNAME': username,
        'SEED_ADMIN_PASSWORD': password,
        'SEED_ADMIN_EMAIL': email,
        'SEED_ADMIN_FULL_NAME': full_name,
        'SEED_MUNICIPALITY_ID': municipality_id,
    }
    if not all(required.values()):
        missing = [key for key, value in required.items() if not value]
        return None, missing

    username = username.strip()
    email = email.strip()
    full_name = full_name.strip()

    if len(username) > 50:
        raise RuntimeError('SEED_ADMIN_USERNAME exceeds 50 characters.')
    if len(email) > 100:
        raise RuntimeError('SEED_ADMIN_EMAIL exceeds 100 characters.')
    if len(full_name) > 100:
        raise RuntimeError('SEED_ADMIN_FULL_NAME exceeds 100 characters.')

    User = _require_model('users')
    Role = _require_model('roles')
    Municipality = _require_model('municipalities')

    if db.session.query(User).filter(User.username == username).first():
        return 'exists', []
    if db.session.query(User).filter(User.email == email).first():
        return 'exists', []

    municipality_pk = int(municipality_id)
    municipality = db.session.get(Municipality, municipality_pk)
    if municipality is None:
        raise RuntimeError(f'municipalities row {municipality_pk} does not exist.')

    admin_role = None
    for row in db.session.query(Role).all():
        if (canonical_role(row.role_name) or row.role_name) == ROLE_ADMINISTRATOR:
            admin_role = row
            break
    if admin_role is None:
        raise RuntimeError('Administrator role is missing.')

    now = datetime.utcnow()
    db.session.add(User(
        municipality_id=municipality_pk,
        role_id=admin_role.role_id,
        username=username,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_active=True,
        is_verified=True,
        failed_login_attempts=0,
        created_at=now,
        updated_at=now,
    ))
    db.session.commit()
    return 'created', []


def main():
    app = create_app()
    with app.app_context():
        created_roles = ensure_roles()
        if created_roles:
            print('Created roles:', ', '.join(created_roles))
        else:
            print('All municipal roles already exist.')

        result, missing = seed_admin_if_configured()
        if result == 'created':
            print('Created Administrator user from seed environment variables.')
        elif result == 'exists':
            print('Seed Administrator already exists; no user was created.')
        else:
            print('Skipped user seed. Set these to create a test Administrator:')
            print(' ', ', '.join(missing))


if __name__ == '__main__':
    main()
