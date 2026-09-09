"""Print auth-related rows without secrets, for Phase 3 verification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.auth.constants import canonical_role
from app.auth.passwords import classify_password_hash
from app.extensions import db
from app.models import get_model, get_reflection_status


def main():
    app = create_app()
    with app.app_context():
        status = get_reflection_status()
        print('Database connected:', status.get('connected'))
        print('Models loaded:', status.get('models_loaded'))
        if not status.get('connected'):
            print('Message:', status.get('message'))
            return

        Role = get_model('roles')
        User = get_model('users')
        SuperAdmin = get_model('super_admins')

        print('\nRoles:')
        if Role is None:
            print('  (not reflected)')
        else:
            rows = db.session.query(Role).all()
            if not rows:
                print('  (none)')
            for row in rows:
                mapped = canonical_role(row.role_name) or row.role_name
                print(f'  {row.role_id}: {row.role_name!r} -> {mapped}')

        print('\nUsers:')
        if User is None:
            print('  (not reflected)')
        else:
            rows = db.session.query(User).all()
            print(f'  count={len(rows)}')
            for row in rows:
                role_name = None
                if Role is not None:
                    role = db.session.get(Role, row.role_id)
                    role_name = role.role_name if role is not None else None
                print(
                    f'  {row.username}  email={row.email}  '
                    f'role={role_name!r}  active={row.is_active}  '
                    f'hash={classify_password_hash(row.password_hash)}'
                )

        print('\nSuper admins:')
        if SuperAdmin is None:
            print('  (not reflected)')
        else:
            rows = db.session.query(SuperAdmin).all()
            print(f'  count={len(rows)}')
            for row in rows:
                print(
                    f'  {row.username}  email={row.email}  '
                    f'active={row.is_active}  '
                    f'hash={classify_password_hash(row.password_hash)}'
                )


if __name__ == '__main__':
    main()
