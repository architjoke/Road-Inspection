import logging
from datetime import datetime

from flask import has_request_context, request, session

from app.extensions import db
from app.models import get_model

logger = logging.getLogger(__name__)


def _clip(value, max_len):
    if value is None:
        return None
    text = str(value)
    return text[:max_len] if text else None


def _request_meta():
    if not has_request_context():
        return {
            'ip_address': None,
            'user_agent': None,
            'session_id': None,
            'request_method': None,
            'request_url': None,
        }

    return {
        'ip_address': _safe_ip(request.remote_addr),
        'user_agent': request.headers.get('User-Agent') or None,
        'session_id': _clip(session.get('_id'), 100),
        'request_method': _clip(request.method, 10),
        'request_url': _clip(request.path, 500),
    }


def _safe_ip(value):
    if not value:
        return None
    candidate = value.split(',')[0].strip()
    if not candidate or candidate.lower() == 'unknown':
        return None
    return candidate


def _write_row(model_name, values):
    Model = get_model(model_name)
    if Model is None:
        return

    try:
        db.session.add(Model(**values))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Failed to write %s row', model_name)


def write_user_audit(
    *,
    action_type,
    action_description,
    user_id=None,
    entity_id=None,
    response_status=None,
    error_message=None,
    old_values=None,
    new_values=None,
):
    now = datetime.utcnow()
    values = {
        'user_id': user_id,
        'action_type': _clip(action_type, 100),
        'entity_type': _clip('auth', 50),
        'entity_id': entity_id,
        'action_description': action_description,
        'old_values': old_values,
        'new_values': new_values,
        'response_status': response_status,
        'error_message': error_message,
        'performed_at': now,
        'created_at': now,
    }
    values.update(_request_meta())
    _write_row('audit_logs', values)


def write_super_admin_audit(
    *,
    action_type,
    action_description,
    super_admin_id,
    municipality_id=None,
    entity_id=None,
    response_status=None,
    error_message=None,
    old_values=None,
    new_values=None,
):
    now = datetime.utcnow()
    values = {
        'super_admin_id': super_admin_id,
        'action_type': _clip(action_type, 100),
        'entity_type': _clip('auth', 50),
        'entity_id': entity_id,
        'municipality_id': municipality_id,
        'action_description': action_description,
        'old_values': old_values,
        'new_values': new_values,
        'response_status': response_status,
        'error_message': error_message,
        'performed_at': now,
        'created_at': now,
    }
    values.update(_request_meta())
    _write_row('super_admin_audit_logs', values)
