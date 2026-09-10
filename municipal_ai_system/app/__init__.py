from flask import Flask, jsonify, redirect, render_template, url_for
from flask_login import current_user
from dotenv import load_dotenv
from pathlib import Path
import logging

from app.config import Config
from app.extensions import csrf, db, limiter, login_manager

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        logger.warning("DATABASE_URL is not set; database features are disabled.")

    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    if app.config.get("SQLALCHEMY_DATABASE_URI"):
        db.init_app(app)

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
                "database_configured": bool(app.config.get("SQLALCHEMY_DATABASE_URI")),
                "phase": "phase_3_merged",
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
        db.session.rollback()
        return render_template(
            "errors/500.html",
            error_title="System Error",
            error_message="An unexpected error occurred. Please try again.",
        ), 500

    from app.auth import auth_bp
    from app.routes import dashboard_bp, inspections_bp, protected_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(protected_bp)

    return app
