import os
import shutil
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = APP_DIR.parent
PROJECT_ROOT = SYSTEM_DIR.parent

DB_URL_PLACEHOLDERS = (
    "USERNAME:PASSWORD",
    "YOUR-RDS-HOST",
    "YOUR_HOST",
)


def _ensure_local_env_file() -> None:
    """Create municipal_ai_system/.env from .env.example if missing.

    Never overwrites an existing .env (so a real DATABASE_URL is preserved).
    """
    env_path = SYSTEM_DIR / ".env"
    example_path = SYSTEM_DIR / ".env.example"
    if env_path.exists() or not example_path.exists():
        return
    shutil.copyfile(example_path, env_path)


def load_project_env() -> None:
    """Load repo-root then municipal_ai_system/.env (local wins). UTF-8 BOM safe."""
    _ensure_local_env_file()
    for path in (PROJECT_ROOT / ".env", SYSTEM_DIR / ".env"):
        if not path.exists():
            continue
        load_dotenv(path, override=True, encoding="utf-8-sig")


def is_usable_database_url(uri: str | None) -> bool:
    uri = (uri or "").strip()
    if not uri:
        return False
    if any(token in uri for token in DB_URL_PLACEHOLDERS):
        return False
    return True


def _offline_flag_from_env_file() -> str | None:
    """Read OFFLINE_MODE from municipal_ai_system/.env only (not start.bat)."""
    env_path = SYSTEM_DIR / ".env"
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "OFFLINE_MODE":
            continue
        return value.strip().strip('"').strip("'").lower()
    return None


def resolve_offline_mode() -> bool:
    """Offline when .env asks for it, or when DATABASE_URL is missing/placeholder.

    start.bat must not force offline when .env already has a real RDS URL.
    Set OFFLINE_MODE=true inside municipal_ai_system/.env for campus Wi-Fi demos.
    """
    load_project_env()
    usable = is_usable_database_url(os.getenv("DATABASE_URL"))
    file_flag = _offline_flag_from_env_file()
    if file_flag in {"true", "1", "yes"}:
        return True
    if file_flag in {"false", "0", "no"}:
        return False
    if usable:
        return False
    explicit = (os.getenv("OFFLINE_MODE") or "").strip().lower()
    return explicit in {"true", "1", "yes"} or not usable


load_project_env()


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

    # Phase 10 — severity rule overrides (defaults live in severity_rules.py)
    SEVERITY_RULE_VERSION = os.getenv("SEVERITY_RULE_VERSION", "phase10-v1")
    try:
        SEVERITY_CONFIDENCE_MIN_REVIEW = float(
            os.getenv("SEVERITY_CONFIDENCE_MIN_REVIEW", "0.35")
        )
    except ValueError:
        SEVERITY_CONFIDENCE_MIN_REVIEW = 0.35
    try:
        SEVERITY_CONFIDENCE_AMBIGUOUS = float(
            os.getenv("SEVERITY_CONFIDENCE_AMBIGUOUS", "0.45")
        )
    except ValueError:
        SEVERITY_CONFIDENCE_AMBIGUOUS = 0.45
    try:
        SEVERITY_SCORE_SEVERE = float(os.getenv("SEVERITY_SCORE_SEVERE", "0.72"))
    except ValueError:
        SEVERITY_SCORE_SEVERE = 0.72
    try:
        SEVERITY_SCORE_MODERATE = float(os.getenv("SEVERITY_SCORE_MODERATE", "0.42"))
    except ValueError:
        SEVERITY_SCORE_MODERATE = 0.42

    # Phase 12 — before/after SSIM verification thresholds
    try:
        VERIFICATION_SSIM_VERIFIED_MAX = float(
            os.getenv("VERIFICATION_SSIM_VERIFIED_MAX", "0.60")
        )
    except ValueError:
        VERIFICATION_SSIM_VERIFIED_MAX = 0.60
    try:
        VERIFICATION_SSIM_PARTIAL_MAX = float(
            os.getenv("VERIFICATION_SSIM_PARTIAL_MAX", "0.85")
        )
    except ValueError:
        VERIFICATION_SSIM_PARTIAL_MAX = 0.85

    # Phase 13 — report export output directory
    REPORTS_OUTPUT_ROOT = Path(
        os.getenv(
            "REPORTS_OUTPUT_ROOT",
            str(APP_DIR / "instance" / "generated_reports"),
        )
    )

    # Phase 14 — security, logging, rate limits (offline-safe defaults)
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR = Path(os.getenv("LOG_DIR", str(APP_DIR / "instance" / "logs")))
    AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", str(LOG_DIR)))
    try:
        LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "2000000"))
    except ValueError:
        LOG_MAX_BYTES = 2_000_000
    try:
        LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    except ValueError:
        LOG_BACKUP_COUNT = 5

    RATELIMIT_ENABLED = os.getenv("RATELIMIT_ENABLED", "true").lower() == "true"
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "100 per hour;20 per minute")
    RATELIMIT_API_SENSITIVE = os.getenv(
        "RATELIMIT_API_SENSITIVE", "30 per minute;200 per hour"
    )
    SECURITY_HEADERS_ENABLED = (
        os.getenv("SECURITY_HEADERS_ENABLED", "true").lower() == "true"
    )
    OFFLINE_MODE = os.getenv("OFFLINE_MODE", "false").lower() == "true"
