from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour", "20 per minute"],
    storage_uri="memory://",
)
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please sign in to continue.'
login_manager.login_message_category = 'warning'
login_manager.session_protection = 'basic'
