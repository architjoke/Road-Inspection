import os
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = APP_DIR.parent
PROJECT_ROOT = SYSTEM_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(SYSTEM_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "development-only-secret-change-before-production"
    )

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    MAX_CONTENT_LENGTH = 100 * 1024 * 1024

    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov"}

    TEMP_UPLOAD_ROOT = APP_DIR / "instance" / "temp" / "inspections"