from flask import Blueprint, jsonify, render_template
from sqlalchemy import text

from app.extensions import db
from app.models import get_reflection_status

main_bp = Blueprint('main', __name__)


@main_bp.route('/health')
def health_check():
    return jsonify({"status": "ok", "message": "API is running"})


@main_bp.route('/db-health')
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
def home():
    return render_template('index.html')
