from app.models.inspection import Inspection
from app.models.inspection_media import InspectionMedia
from app.models.municipality import Municipality
from app.models.reflection import (
    ModelRegistry,
    get_model as _reflected_get_model,
    get_reflection_status,
    reflect_models,
)
from app.models.role import Role
from app.models.tables import EXPLICIT_ORM_TABLES, SPATIAL_TABLES, TABLE_NAMES
from app.models.user import User
from app.models.ward import Ward

EXPLICIT_MODELS = {
    "roles": Role,
    "users": User,
    "municipalities": Municipality,
    "wards": Ward,
    "inspections": Inspection,
    "inspection_media": InspectionMedia,
}


def get_model(table_name: str):
    if table_name in EXPLICIT_MODELS:
        return EXPLICIT_MODELS[table_name]
    return _reflected_get_model(table_name)


__all__ = [
    "Role",
    "User",
    "Municipality",
    "Ward",
    "Inspection",
    "InspectionMedia",
    "ModelRegistry",
    "TABLE_NAMES",
    "SPATIAL_TABLES",
    "EXPLICIT_ORM_TABLES",
    "get_model",
    "get_reflection_status",
    "reflect_models",
]
