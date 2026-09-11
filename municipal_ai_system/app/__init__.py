from flask import Flask, jsonify, redirect, render_template, url_for
from flask_login import current_user
from pathlib import Path
import logging

from app.config import Config
from app.extensions import csrf, db, limiter, login_manager

BASE_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


def _database_configured(app: Flask) -> bool:
    uri = (app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()
    if not uri:
        return False
    # Treat unedited .env.example placeholders as "not configured"
    placeholders = ("USERNAME:PASSWORD", "YOUR-RDS-HOST", "YOUR_HOST", "change-me")
    return not any(token in uri for token in placeholders)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db_ok = _database_configured(app)
    app.config["DATABASE_CONFIGURED"] = db_ok

    if not db_ok:
        logger.error(
            "DATABASE_URL is missing or still a placeholder. "
            "Edit municipal_ai_system/.env (copied from .env.example if needed), "
            "set a real DATABASE_URL, then restart the app. "
            "Login will not work until the database is configured."
        )

    # Always register extensions — never skip db.init_app
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    db.init_app(app)

    if db_ok:
        with app.app_context():
            from app.models import reflect_models

            try:
                reflect_models()
            except Exception as exc:
                logger.warning(
                    "Model reflection deferred until database is reachable: %s",
                    exc,
                )

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "application": "AI Road Inspection System",
                "database_configured": bool(app.config.get("DATABASE_CONFIGURED")),
                "phase": "phase_4_5_frontend_storage",
            }
        ), 200

    @app.errorhandler(401)
    def unauthorized_error(_error):
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found_error(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def file_too_large_error(_error):
        return render_template(
            "errors/500.html",
            error_title="File Too Large",
            error_message=(
                "The selected file is larger than the maximum "
                "allowed size of 100 MB."
            ),
        ), 413

    @app.errorhandler(500)
    def internal_error(_error):
        if app.config.get("DATABASE_CONFIGURED"):
            try:
                db.session.rollback()
            except Exception:
                logger.exception("Failed to roll back DB session after 500 error")
        return render_template(
            "errors/500.html",
            error_title="System Error",
            error_message="An unexpected error occurred. Please try again.",
        ), 500

    # Phase-3 auth from app.auth (not app.routes.auth)
    from app.auth import auth_bp
    from app.routes import api_bp, dashboard_bp, inspections_bp, protected_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(protected_bp)
    app.register_blueprint(api_bp)

    return app
