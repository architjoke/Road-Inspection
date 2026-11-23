from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from pathlib import Path
import logging
import os

from app.config import Config, is_usable_database_url, resolve_offline_mode
from app.extensions import csrf, db, limiter, login_manager


BASE_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


def _database_configured(app: Flask) -> bool:
    return is_usable_database_url(app.config.get("SQLALCHEMY_DATABASE_URI"))


def _wants_json_response() -> bool:
    if request.path.startswith("/api/") or request.path == "/health":
        return True
    best = request.accept_mimetypes.best
    if best == "application/json":
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] >= accept["text/html"]


def create_app(config_overrides: dict | None = None):
    from app.config import DEFAULT_INSTANCE_DIR

    app = Flask(__name__, instance_path=str(Path(DEFAULT_INSTANCE_DIR).resolve()))
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    # Offline/pytest: never attempt remote DB reflection.
    if config_overrides and "TESTING" in config_overrides:
        app.config["TESTING"] = bool(config_overrides.get("TESTING"))

    if config_overrides and "OFFLINE_MODE" in config_overrides:
        offline_env = bool(config_overrides.get("OFFLINE_MODE"))
    elif app.config.get("TESTING"):
        offline_env = True
    else:
        offline_env = resolve_offline_mode()

    app.config["OFFLINE_MODE"] = bool(offline_env)

    if app.config.get("TESTING") or offline_env:
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {}
        app.config["DATABASE_CONFIGURED"] = False
        app.config["OFFLINE_MODE"] = True
        db_ok = False
    else:
        db_ok = _database_configured(app)
        app.config["DATABASE_CONFIGURED"] = db_ok
        app.config["OFFLINE_MODE"] = False

    from app.services.logging_setup import setup_logging

    setup_logging(app)

    if not db_ok:
        if app.config.get("TESTING") or app.config.get("OFFLINE_MODE"):
            logger.info("Offline/TESTING: DB reflection disabled (sqlite memory).")
        else:
            logger.error(
                "DATABASE_URL missing/placeholder. Set it in .env (project root) "
                "or use OFFLINE_MODE=true for local demo."
            )

    for key in ("TEMP_UPLOAD_ROOT", "REPORTS_OUTPUT_ROOT", "LOG_DIR", "AUDIT_LOG_DIR"):
        value = app.config.get(key)
        if value:
            try:
                Path(value).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.debug("Could not create %s=%s", key, value, exc_info=True)

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
                app.config["DATABASE_REACHABLE"] = False
            else:
                app.config["DATABASE_REACHABLE"] = True
    else:
        app.config["DATABASE_REACHABLE"] = False

    if (
        app.config.get("OFFLINE_MODE")
        or app.config.get("TESTING")
        or not db_ok
        or not app.config.get("DATABASE_REACHABLE", True)
    ):
        with app.app_context():
            try:
                from app.services.directory_service import seed_offline_demo_accounts

                seed_offline_demo_accounts()
            except Exception:
                logger.exception("Could not seed offline demo accounts")

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

    from app.auth import auth_bp
    from app.routes import api_bp, dashboard_bp, inspections_bp, protected_bp
    from app.routes.contractor import contractor_bp
    from app.routes.training import training_bp
    from app.routes.field_worker import field_worker_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(inspections_bp)
    app.register_blueprint(protected_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(contractor_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(field_worker_bp)

    @app.context_processor
    def _cost_rate_helpers():
        def cost_rates_are_placeholder() -> bool:
            """True while repair_cost_rates is unset in system_settings (cached 5 min)."""
            try:
                from app.ai.cost_engine import get_cost_rates

                return get_cost_rates().get("source") == "placeholder_defaults"
            except Exception:
                return True

        return {"cost_rates_are_placeholder": cost_rates_are_placeholder}

    logger.info(
        "Application ready offline_mode=%s database_configured=%s",
        bool(app.config.get("OFFLINE_MODE")),
        bool(app.config.get("DATABASE_CONFIGURED")),
    )
    return app
