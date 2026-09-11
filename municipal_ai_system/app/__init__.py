from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from pathlib import Path
import logging
import os

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


def _wants_json_response() -> bool:
    if request.path.startswith("/api/") or request.path == "/health":
        return True
    best = request.accept_mimetypes.best
    if best == "application/json":
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] >= accept["text/html"]


def create_app(config_overrides: dict | None = None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    # Offline/pytest: never attempt remote DB reflection.
    offline_env = os.getenv("OFFLINE_MODE", "").lower() == "true"
    if config_overrides and "OFFLINE_MODE" in config_overrides:
        offline_env = bool(config_overrides.get("OFFLINE_MODE"))
    if offline_env:
        app.config["OFFLINE_MODE"] = True

    if (
        app.config.get("TESTING")
        or app.config.get("OFFLINE_MODE")
        or offline_env
    ):
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {}
        app.config["DATABASE_CONFIGURED"] = False
        app.config["OFFLINE_MODE"] = True
        db_ok = False
    else:
        db_ok = _database_configured(app)
        app.config["DATABASE_CONFIGURED"] = db_ok

    # Phase 14 — structured rotating logs (local files only)
    from app.services.logging_setup import setup_logging

    setup_logging(app)

    if not db_ok:
        if app.config.get("TESTING") or app.config.get("OFFLINE_MODE"):
            logger.info(
                "Offline/TESTING mode: database reflection disabled "
                "(sqlite memory; no RDS/Aurora)."
            )
        else:
            logger.error(
                "DATABASE_URL is missing or still a placeholder. "
                "Edit municipal_ai_system/.env (copied from .env.example if needed), "
                "set a real DATABASE_URL, then restart the app. "
                "Login will not work until the database is configured. "
                "For local acceptance without RDS, set OFFLINE_MODE=true."
            )

    # Ensure local storage folders exist (offline launch readiness)
    for key in ("TEMP_UPLOAD_ROOT", "REPORTS_OUTPUT_ROOT", "LOG_DIR", "AUDIT_LOG_DIR"):
        value = app.config.get(key)
        if value:
            try:
                Path(value).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.debug("Could not create %s=%s", key, value, exc_info=True)

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

    @app.after_request
    def apply_security_headers(response):
        if not app.config.get("SECURITY_HEADERS_ENABLED", True):
            return response
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(self), camera=(self), microphone=()"
        )
        if not app.config.get("TESTING"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self' https: data: blob: 'unsafe-inline' 'unsafe-eval'",
            )
        return response

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        from app.services.health_service import build_health_payload

        payload = build_health_payload(app)
        code = 200 if payload.get("status") in {"ok", "degraded"} else 503
        return jsonify(payload), code

    @app.errorhandler(401)
    def unauthorized_error(_error):
        if _wants_json_response():
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden(_error):
        if _wants_json_response():
            return jsonify({"error": "Forbidden."}), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found_error(_error):
        if _wants_json_response():
            return jsonify({"error": "Not found."}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def file_too_large_error(_error):
        message = (
            "The selected file is larger than the maximum allowed size of 500 MB."
        )
        if _wants_json_response():
            return jsonify({"error": message}), 413
        return render_template(
            "errors/500.html",
            error_title="File Too Large",
            error_message=message,
        ), 413

    @app.errorhandler(429)
    def rate_limit_error(error):
        message = "Too many requests. Please slow down and try again."
        description = getattr(error, "description", None) or message
        if _wants_json_response():
            return jsonify({"error": description, "code": 429}), 429
        return render_template("errors/429.html", error_message=description), 429

    @app.errorhandler(500)
    def internal_error(_error):
        if app.config.get("DATABASE_CONFIGURED"):
            try:
                db.session.rollback()
            except Exception:
                logger.exception("Failed to roll back DB session after 500 error")
        if _wants_json_response():
            return jsonify({"error": "An unexpected error occurred."}), 500
        return render_template(
            "errors/500.html",
            error_title="System Error",
            error_message="An unexpected error occurred. Please try again.",
        ), 500

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        # Let HTTPExceptions fall through to specific handlers when possible
        from werkzeug.exceptions import HTTPException

        if isinstance(error, HTTPException):
            return error
        logger.exception("Unhandled exception on %s", request.path)
        if _wants_json_response():
            return jsonify({"error": "An unexpected error occurred."}), 500
        if app.config.get("DATABASE_CONFIGURED"):
            try:
                db.session.rollback()
            except Exception:
                pass
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

    logger.info(
        "Application ready offline_mode=%s database_configured=%s",
        bool(app.config.get("OFFLINE_MODE")),
        bool(app.config.get("DATABASE_CONFIGURED")),
    )
    return app
