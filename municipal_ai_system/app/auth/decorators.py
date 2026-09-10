from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from app.auth.audit import write_super_admin_audit, write_user_audit


def _deny(message):
    if current_user.is_authenticated:
        if getattr(current_user, 'is_super_admin', False):
            write_super_admin_audit(
                action_type='ACCESS_DENIED',
                action_description=message,
                super_admin_id=current_user.pk,
                entity_id=current_user.pk,
                response_status=403,
                error_message='forbidden',
            )
        else:
            write_user_audit(
                action_type='ACCESS_DENIED',
                action_description=message,
                user_id=current_user.pk,
                entity_id=current_user.pk,
                response_status=403,
                error_message='forbidden',
            )
    abort(403)


def role_required(*role_names):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if getattr(current_user, 'is_super_admin', False) or not current_user.has_role(*role_names):
                _deny('Role is not allowed to access this page.')
            return view(*args, **kwargs)

        return login_required(wrapped)

    return decorator


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, 'is_super_admin', False):
            _deny('Super admin access is required.')
        return view(*args, **kwargs)

    return login_required(wrapped)
