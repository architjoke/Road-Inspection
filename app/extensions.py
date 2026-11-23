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
# Limits are per client IP and count every page, map JSON call and upload poll,
# so the default must allow a busy session (or an office behind one NAT IP);
# login, upload and compare keep their own strict per-route limits.
_default_limits = [
    part.strip()
    for part in os.getenv(
        "RATELIMIT_DEFAULT", "2000 per hour;200 per minute"
    ).split(";")
    if part.strip()
]
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=_default_limits or ["2000 per hour", "200 per minute"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "warning"
login_manager.session_protection = "basic"
