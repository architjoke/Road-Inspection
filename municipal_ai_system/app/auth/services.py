from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import func, or_

from app.auth.audit import write_super_admin_audit, write_user_audit
from app.auth.identity import identity_from_super_admin, identity_from_user
from app.auth.passwords import check_password
from app.extensions import db
from app.models import get_model

GENERIC_LOGIN_ERROR = 'Invalid username or password.'
ACCOUNT_DISABLED = 'This account is disabled.'
DATABASE_UNAVAILABLE = 'Database is not available. Try again later.'


def _identifier_filter(model, identifier):
    value = identifier.lower()
    return or_(
        func.lower(func.trim(model.email)) == value,
        func.lower(func.trim(model.username)) == value,
    )


def _is_locked(user_row, now):
    locked_until = getattr(user_row, 'locked_until', None)
    return locked_until is not None and locked_until > now


def _record_failed_user_login(user_row, now):
    attempts = (user_row.failed_login_attempts or 0) + 1
    user_row.failed_login_attempts = attempts
    user_row.updated_at = now

    max_attempts = current_app.config.get('AUTH_MAX_FAILED_ATTEMPTS', 5)
    lockout_minutes = current_app.config.get('AUTH_LOCKOUT_MINUTES', 15)
    if attempts >= max_attempts:
        user_row.locked_until = now + timedelta(minutes=lockout_minutes)

    db.session.commit()


def _clear_user_lockout(user_row, now):
    user_row.failed_login_attempts = 0
    user_row.locked_until = None
    user_row.last_login = now
    user_row.updated_at = now
    db.session.commit()


def authenticate(identifier, password):
    identifier = (identifier or '').strip()
    if not identifier or not password:
        return None, GENERIC_LOGIN_ERROR

    User = get_model('users')
    SuperAdmin = get_model('super_admins')
    if User is None and SuperAdmin is None:
        return None, DATABASE_UNAVAILABLE

    now = datetime.utcnow()

    user_row = None
    if User is not None:
        user_row = db.session.query(User).filter(_identifier_filter(User, identifier)).first()

    if user_row is not None:
        if user_row.is_active is False:
            write_user_audit(
                action_type='LOGIN_FAILURE',
                action_description='Login rejected because the account is disabled.',
                user_id=user_row.user_id,
                entity_id=user_row.user_id,
                response_status=403,
                error_message='account_disabled',
            )
            return None, ACCOUNT_DISABLED

        if _is_locked(user_row, now):
            write_user_audit(
                action_type='LOGIN_FAILURE',
                action_description='Login rejected because the account is locked.',
                user_id=user_row.user_id,
                entity_id=user_row.user_id,
                response_status=423,
                error_message='account_locked',
            )
            until = user_row.locked_until.strftime('%Y-%m-%d %H:%M UTC')
            return None, f'Account is locked until {until}.'

        if not check_password(password, user_row.password_hash):
            _record_failed_user_login(user_row, now)
            write_user_audit(
                action_type='LOGIN_FAILURE',
                action_description='Login failed due to invalid password.',
                user_id=user_row.user_id,
                entity_id=user_row.user_id,
                response_status=401,
                error_message='invalid_password',
            )
            return None, GENERIC_LOGIN_ERROR

        _clear_user_lockout(user_row, now)
        identity = identity_from_user(user_row)
        write_user_audit(
            action_type='LOGIN_SUCCESS',
            action_description='User signed in.',
            user_id=user_row.user_id,
            entity_id=user_row.user_id,
            response_status=200,
        )
        return identity, None

    admin_row = None
    if SuperAdmin is not None:
        admin_row = (
            db.session.query(SuperAdmin)
            .filter(_identifier_filter(SuperAdmin, identifier))
            .first()
        )

    if admin_row is not None:
        if admin_row.is_active is False:
            write_super_admin_audit(
                action_type='LOGIN_FAILURE',
                action_description='Login rejected because the account is disabled.',
                super_admin_id=admin_row.super_admin_id,
                entity_id=admin_row.super_admin_id,
                response_status=403,
                error_message='account_disabled',
            )
            return None, ACCOUNT_DISABLED

        if not check_password(password, admin_row.password_hash):
            write_super_admin_audit(
                action_type='LOGIN_FAILURE',
                action_description='Login failed due to invalid password.',
                super_admin_id=admin_row.super_admin_id,
                entity_id=admin_row.super_admin_id,
                response_status=401,
                error_message='invalid_password',
            )
            return None, GENERIC_LOGIN_ERROR

        admin_row.last_login = now
        admin_row.updated_at = now
        db.session.commit()
        identity = identity_from_super_admin(admin_row)
        write_super_admin_audit(
            action_type='LOGIN_SUCCESS',
            action_description='Super admin signed in.',
            super_admin_id=admin_row.super_admin_id,
            entity_id=admin_row.super_admin_id,
            response_status=200,
        )
        return identity, None

    write_user_audit(
        action_type='LOGIN_FAILURE',
        action_description='Login failed because no matching account was found.',
        response_status=401,
        error_message='unknown_identifier',
    )
    return None, GENERIC_LOGIN_ERROR
