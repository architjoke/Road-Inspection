from flask import Flask, render_template
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

    if not app.config.get('SQLALCHEMY_DATABASE_URI'):
        logger.warning('DATABASE_URL is not set; database features are disabled.')

    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    if app.config.get('SQLALCHEMY_DATABASE_URI'):
        db.init_app(app)

        with app.app_context():
            from app.models import reflect_models

            try:
                reflect_models()
            except Exception as exc:
                logger.warning('Model reflection deferred until database is reachable: %s', exc)

    from app.auth import auth_bp
    from app.routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template('403.html'), 403

    return app
