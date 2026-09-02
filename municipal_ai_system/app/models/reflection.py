import logging
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import Table, inspect, text, event

from app.extensions import db
from app.models.tables import SPATIAL_TABLES, TABLE_NAMES

logger = logging.getLogger(__name__)

_reflected = False
ModelRegistry: dict[str, type] = {}
_postgis_listener_registered = False


def _configure_postgis_reflection() -> None:
    """Map reflected geometry columns to GeoAlchemy2 types."""
    global _postgis_listener_registered
    if _postgis_listener_registered:
        return

    @event.listens_for(Table, 'column_reflect')
    def _receive_column_reflect(_inspector, _table, column_info):
        reflected_type = column_info.get('type')
        if reflected_type is None:
            return

        type_name = str(reflected_type).lower()
        if 'geometry' in type_name or 'geography' in type_name:
            column_info['type'] = Geometry(geometry_type='GEOMETRY', srid=4326)

    _postgis_listener_registered = True


def _class_name_for_table(tablename: str) -> str:
    return ''.join(part.capitalize() for part in tablename.split('_'))


def _table_key(table_name: str) -> Optional[str]:
    if f'public.{table_name}' in db.metadata.tables:
        return f'public.{table_name}'
    if table_name in db.metadata.tables:
        return table_name
    return None


def _clear_reflected_metadata() -> None:
    for table_name in TABLE_NAMES:
        for key in (f'public.{table_name}', table_name):
            if key in db.metadata.tables:
                db.metadata.remove(db.metadata.tables[key])


def reflect_models(force: bool = False) -> dict[str, type]:
    """
    Reflect existing DBeaver/RDS tables into SQLAlchemy ORM classes.
    Does not create or alter database objects.
    """
    global _reflected

    if _reflected and not force:
        return ModelRegistry

    if not db.engine:
        logger.warning('Database engine is not configured; skipping model reflection.')
        return ModelRegistry

    if force:
        _reflected = False
        ModelRegistry.clear()
        _clear_reflected_metadata()

    _configure_postgis_reflection()

    db.metadata.reflect(
        bind=db.engine,
        schema='public',
        only=TABLE_NAMES,
        extend_existing=True,
        views=False,
    )

    reflected_tables = {
        name for name in TABLE_NAMES if _table_key(name) is not None
    }
    missing_tables = sorted(set(TABLE_NAMES) - reflected_tables)
    if missing_tables:
        logger.warning('Missing tables during reflection: %s', ', '.join(missing_tables))

    ModelRegistry.clear()
    for table_name in TABLE_NAMES:
        key = _table_key(table_name)
        if key is None:
            continue

        table = db.metadata.tables[key]
        class_name = _class_name_for_table(table_name)
        model_class = type(
            class_name,
            (db.Model,),
            {
                '__table__': table,
                '__tablename__': table_name,
                '__module__': 'app.models.dynamic',
            },
        )
        ModelRegistry[table_name] = model_class

    if not ModelRegistry:
        logger.error(
            'Reflection completed but no ORM models were mapped. '
            'Metadata tables: %s',
            list(db.metadata.tables.keys()),
        )
        return ModelRegistry

    _reflected = True
    logger.info('Reflected %d ORM models from existing database schema.', len(ModelRegistry))
    return ModelRegistry


def get_model(table_name: str) -> Optional[type]:
    return ModelRegistry.get(table_name)


def get_reflection_status() -> dict:
    """Return reflection status for health checks."""
    if not db.engine:
        return {
            'connected': False,
            'reflected': False,
            'expected_tables': len(TABLE_NAMES),
            'found_tables': 0,
            'missing_tables': TABLE_NAMES,
            'message': 'DATABASE_URL is not configured.',
        }

    try:
        inspector_tables = set(inspect(db.engine).get_table_names(schema='public'))
    except Exception as exc:
        return {
            'connected': False,
            'reflected': _reflected,
            'expected_tables': len(TABLE_NAMES),
            'found_tables': 0,
            'missing_tables': TABLE_NAMES,
            'message': str(exc),
        }

    found = sorted(set(TABLE_NAMES) & inspector_tables)
    missing = sorted(set(TABLE_NAMES) - inspector_tables)

    postgis_enabled = False
    try:
        result = db.session.execute(
            text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'postgis')")
        ).scalar()
        postgis_enabled = bool(result)
    except Exception:
        postgis_enabled = False

    spatial_indexes = []
    if postgis_enabled and found:
        try:
            rows = db.session.execute(
                text(
                    """
                    SELECT tablename, indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = ANY(:tables)
                      AND indexdef ILIKE '%USING gist%'
                    ORDER BY tablename, indexname
                    """
                ),
                {'tables': list(SPATIAL_TABLES & set(found))},
            ).all()
            spatial_indexes = [
                {'table': row[0], 'index': row[1]} for row in rows
            ]
        except Exception:
            spatial_indexes = []

    return {
        'connected': True,
        'reflected': _reflected,
        'expected_tables': len(TABLE_NAMES),
        'found_tables': len(found),
        'missing_tables': missing,
        'postgis_enabled': postgis_enabled,
        'spatial_indexes': spatial_indexes,
        'models_loaded': len(ModelRegistry),
    }
