from flask import Blueprint

auth_bp = Blueprint('auth', __name__)

from app.auth import identity as _identity  # noqa: E402,F401
from app.auth import routes as _routes  # noqa: E402,F401
