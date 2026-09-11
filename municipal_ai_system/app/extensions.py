from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy.orm import DeclarativeBase
import os


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()

# Offline-safe in-memory rate limit store by default (no Redis required).
_default_limits = [
    part.strip()
    for part in os.getenv(
        "RATELIMIT_DEFAULT", "100 per hour;20 per minute"
    ).split(";")
    if part.strip()
]
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=_default_limits or ["100 per hour", "20 per minute"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "warning"
login_manager.session_protection = "basic"
