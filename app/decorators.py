from functools import wraps

from flask import abort
from flask_login import current_user


def roles_required(*allowed_roles: str):
    """
    Restrict a route to the exact lowercase role values in PostgreSQL.

    Example:
    @roles_required("super_admin", "municipal_admin")
    """
    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            if not current_user.has_role(*allowed_roles):
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator