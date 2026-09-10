from flask import Flask, jsonify, redirect, render_template, url_for
from flask_login import current_user

from app.config import Config
from app.extensions import db, login_manager


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError(
            "DATABASE_URL is missing. Check the root .env file."
        )

    db.init_app(app)
    login_manager.init_app(app)

    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

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
                "database_configured": True,
                "phase": "phase_4_inspection_upload"
            }
        ), 200

    @app.errorhandler(401)
    def unauthorized_error(error):
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden_error(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def file_too_large_error(error):
        return render_template(
            "errors/500.html",
            error_title="File Too Large",
            error_message=(
                "The selected file is larger than the maximum "
                "allowed size of 100 MB."
            )
        ), 413

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()

        return render_template(
            "errors/500.html",
            error_title="System Error",
            error_message=(
                "An unexpected error occurred. Please try again."
            )
        ), 500

    from app.routes import (
        auth_bp,
        dashboard_bp,
        inspections_bp,
        protected_bp,
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(protected_bp)

    return app