"""Shreya UI blueprints. Phase-3 auth lives in app.auth — do not re-export auth_bp here."""

from app.routes.api import api_bp
from app.routes.dashboard import dashboard_bp
from app.routes.inspections import inspections_bp
from app.routes.protected import protected_bp

__all__ = [
    "api_bp",
    "dashboard_bp",
    "inspections_bp",
    "protected_bp",
]
