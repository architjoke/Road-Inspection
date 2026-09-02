from flask import Flask
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from pathlib import Path
import logging
import os

from app.config import Config
from app.extensions import db

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour", "20 per minute"],
    storage_uri="memory://",
)

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get('SQLALCHEMY_DATABASE_URI'):
        logger.warning('DATABASE_URL is not set; database features are disabled.')

    csrf.init_app(app)
    limiter.init_app(app)

    if app.config.get('SQLALCHEMY_DATABASE_URI'):
        db.init_app(app)

        with app.app_context():
            from app.models import reflect_models

            try:
                reflect_models()
            except Exception as exc:
                logger.warning('Model reflection deferred until database is reachable: %s', exc)

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
