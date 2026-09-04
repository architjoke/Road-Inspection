from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import text

from app.auth.constants import (
    ROLE_ADMINISTRATOR,
    ROLE_CONTRACTOR,
    ROLE_FIELD_OFFICER,
    ROLE_FIELD_WORKER,
    ROLE_VIEWER,
)
from app.auth.decorators import role_required, super_admin_required
from app.extensions import db, limiter
from app.models import get_reflection_status

main_bp = Blueprint('main', __name__)


@main_bp.route('/health')
@limiter.exempt
def health_check():
    return jsonify({"status": "ok", "message": "API is running"})


@main_bp.route('/db-health')
@limiter.exempt
def db_health():
    from app.models import reflect_models

    status = get_reflection_status()
    if status.get('connected') and not status.get('reflected'):
        try:
            reflect_models(force=True)
            status = get_reflection_status()
        except Exception as exc:
            status['reflection_error'] = str(exc)

    if not status.get('connected'):
        return jsonify({
            "status": "error",
            "message": status.get('message', 'Database connection failed'),
            "database": status,
        }), 503

    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
            "database": status,
        }), 503

    all_tables_present = status.get('found_tables', 0) == status.get('expected_tables', 20)
    response_status = "ok" if all_tables_present else "degraded"

    return jsonify({
        "status": response_status,
        "message": "Database connected",
        "database": status,
    }), 200 if all_tables_present else 207


@main_bp.route('/')
@login_required
def home():
    return render_template('index.html')


@main_bp.route('/admin')
@role_required(ROLE_ADMINISTRATOR)
def admin_workspace():
    return render_template(
        'role_workspace.html',
        title='Administrator workspace',
        description='Review detections, assign tasks, and approve repairs. Full dashboard arrives in Phase 4.',
    )


@main_bp.route('/field-worker')
@role_required(ROLE_FIELD_WORKER)
def field_worker_workspace():
    return render_template(
        'role_workspace.html',
        title='Field Worker workspace',
        description='Capture and upload road inspections. Upload flow arrives in Phase 4.',
    )


@main_bp.route('/field-officer')
@role_required(ROLE_FIELD_OFFICER)
def field_officer_workspace():
    return render_template(
        'role_workspace.html',
        title='Field Officer workspace',
        description='Verify detections on site and update inspection status. Task views arrive in Phase 4.',
    )


@main_bp.route('/contractor')
@role_required(ROLE_CONTRACTOR)
def contractor_workspace():
    return render_template(
        'role_workspace.html',
        title='Contractor workspace',
        description='Record repair work and after photos. Repair manager arrives in Phase 4.',
    )


@main_bp.route('/viewer')
@role_required(ROLE_VIEWER)
def viewer_workspace():
    return render_template(
        'role_workspace.html',
        title='Viewer workspace',
        description='Read-only access to inspections and reports. Dashboards arrive in Phase 4.',
    )


@main_bp.route('/super-admin')
@super_admin_required
def super_admin_workspace():
    municipality_count = len(getattr(current_user, 'municipality_ids', []) or [])
    return render_template(
        'role_workspace.html',
        title='Super Admin workspace',
        description=(
            f'Cross-municipality oversight is active for {municipality_count} '
            'assigned municipality(ies). Management UI arrives in a later phase.'
        ),
    )
