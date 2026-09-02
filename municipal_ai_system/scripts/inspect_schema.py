"""Inspect the live PostgreSQL schema without modifying it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models.tables import TABLE_NAMES


def main() -> None:
    app = create_app()

    with app.app_context():
        if not db.engine:
            print('DATABASE_URL is not configured.')
            return

        db.session.execute(text('SELECT 1'))
        print('Connected to database.\n')

        inspector = inspect(db.engine)
        existing = set(inspector.get_table_names(schema='public'))

        print('Expected tables (20):')
        for name in TABLE_NAMES:
            marker = 'OK' if name in existing else 'MISSING'
            print(f'  [{marker}] {name}')

        print('\nColumns:')
        for name in TABLE_NAMES:
            if name not in existing:
                continue
            columns = inspector.get_columns(name, schema='public')
            print(f'\n{name}:')
            for column in columns:
                print(f"  - {column['name']}: {column['type']}")

        print('\nForeign keys:')
        for name in TABLE_NAMES:
            if name not in existing:
                continue
            fks = inspector.get_foreign_keys(name, schema='public')
            if not fks:
                continue
            print(f'\n{name}:')
            for fk in fks:
                print(
                    f"  - {fk['constrained_columns']} -> "
                    f"{fk['referred_table']}.{fk['referred_columns']}"
                )


if __name__ == '__main__':
    main()
