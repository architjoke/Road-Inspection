import os
import shutil
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = APP_DIR.parent
PROJECT_ROOT = SYSTEM_DIR.parent


def _ensure_local_env_file() -> None:
    """Create municipal_ai_system/.env from .env.example if missing.

    Never overwrites an existing .env (so a real DATABASE_URL is preserved).
    """
    env_path = SYSTEM_DIR / ".env"
    example_path = SYSTEM_DIR / ".env.example"
    if env_path.exists() or not example_path.exists():
        return
    shutil.copyfile(example_path, env_path)


_ensure_local_env_file()

# Repo-root .env first, then municipal_ai_system/.env (local wins on conflicts).
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(SYSTEM_DIR / ".env", override=True)


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "development-only-secret-change-before-production"
    )

    # DATABASE_URL in .env → SQLAlchemy URI
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
    }

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=7)

    AUTH_MAX_FAILED_ATTEMPTS = int(os.getenv("AUTH_MAX_FAILED_ATTEMPTS", "5"))
    AUTH_LOCKOUT_MINUTES = int(os.getenv("AUTH_LOCKOUT_MINUTES", "15"))

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov"}
    TEMP_UPLOAD_ROOT = APP_DIR / "instance" / "temp" / "inspections"

    # ImageKit CDN (Phase 5) — not MinIO/S3
    IMAGEKIT_PUBLIC_KEY = os.getenv("IMAGEKIT_PUBLIC_KEY", "")
    IMAGEKIT_PRIVATE_KEY = os.getenv("IMAGEKIT_PRIVATE_KEY", "")
    IMAGEKIT_URL_ENDPOINT = os.getenv("IMAGEKIT_URL_ENDPOINT", "")

    # Phase 6/7 — YOLO analysis
    YOLO_MODEL_PATH = os.getenv(
        "YOLO_MODEL_PATH",
        str(APP_DIR / "ai_models" / "best.pt"),
    )
    YOLO_CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", "0.25"))
    YOLO_IOU = float(os.getenv("YOLO_IOU", "0.45"))
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", "640"))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
    YOLO_FORCE_MOCK = os.getenv("YOLO_FORCE_MOCK", "false").lower() == "true"
    ANALYSIS_MAX_WORKERS = int(os.getenv("ANALYSIS_MAX_WORKERS", "2"))

    # Phase 8 — optional pixel segmentation (disabled by default)
    SEGMENTATION_ENABLED = os.getenv("SEGMENTATION_ENABLED", "false").lower() == "true"
    SEGMENTATION_FORCE_MOCK = os.getenv("SEGMENTATION_FORCE_MOCK", "true").lower() == "true"
    SEGMENTATION_MODEL_PATH = os.getenv("SEGMENTATION_MODEL_PATH", "") or ""
    SEGMENTATION_DEVICE = os.getenv("SEGMENTATION_DEVICE", "cpu")
    try:
        SEGMENTATION_CONFIDENCE_THRESHOLD = float(
            os.getenv("SEGMENTATION_CONFIDENCE_THRESHOLD", "0.25")
        )
    except ValueError:
        SEGMENTATION_CONFIDENCE_THRESHOLD = 0.25
    try:
        SEGMENTATION_IMAGE_SIZE = int(os.getenv("SEGMENTATION_IMAGE_SIZE", "640"))
    except ValueError:
        SEGMENTATION_IMAGE_SIZE = 640
    SEGMENTATION_OUTPUT_DIR = os.getenv("SEGMENTATION_OUTPUT_DIR", "") or ""

    # Phase 9 — optional monocular depth (disabled by default; relative only)
    DEPTH_ENABLED = os.getenv("DEPTH_ENABLED", "false").lower() == "true"
    DEPTH_FORCE_MOCK = os.getenv("DEPTH_FORCE_MOCK", "true").lower() == "true"
    DEPTH_MODEL_PATH = os.getenv("DEPTH_MODEL_PATH", "") or ""
    DEPTH_MODEL_TYPE = os.getenv("DEPTH_MODEL_TYPE", "relative_mock")
    DEPTH_DEVICE = os.getenv("DEPTH_DEVICE", "cpu")
    try:
        DEPTH_IMAGE_SIZE = int(os.getenv("DEPTH_IMAGE_SIZE", "384"))
    except ValueError:
        DEPTH_IMAGE_SIZE = 384
    DEPTH_OUTPUT_DIR = os.getenv("DEPTH_OUTPUT_DIR", "") or ""
    DEPTH_NORMALIZATION_MODE = os.getenv("DEPTH_NORMALIZATION_MODE", "relative")
    try:
        DEPTH_MIN_CONFIDENCE = float(os.getenv("DEPTH_MIN_CONFIDENCE", "0.0"))
    except ValueError:
        DEPTH_MIN_CONFIDENCE = 0.0
