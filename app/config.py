import os
import shutil
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = APP_DIR.parent
PROJECT_ROOT = SYSTEM_DIR  # repository root = project root
# Runtime data (logs, uploads, reports, caches) — outside the code package.
DEFAULT_INSTANCE_DIR = Path(os.getenv("INSTANCE_DIR") or (SYSTEM_DIR / "instance"))

DB_URL_PLACEHOLDERS = (
    "USERNAME:PASSWORD",
    "YOUR-RDS-HOST",
    "YOUR-SUPABASE-HOST",
    "YOUR_HOST",
)


def _ensure_local_env_file() -> None:
    """Create .env from .env.example if missing; never overwrite existing."""
    env_path = SYSTEM_DIR / ".env"
    example_path = SYSTEM_DIR / ".env.example"
    if env_path.exists() or not example_path.exists():
        return
    shutil.copyfile(example_path, env_path)


def load_project_env() -> None:
    """Load the project-root .env (UTF-8 BOM safe)."""
    _ensure_local_env_file()
    for path in (SYSTEM_DIR / ".env",):
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
    """Read OFFLINE_MODE from the project-root .env only (not start.bat)."""
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
    """Offline when .env sets OFFLINE_MODE, or DATABASE_URL is missing/placeholder."""
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


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


load_project_env()


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "development-only-secret-change-before-production",
    )

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
    }

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=7)

    AUTH_MAX_FAILED_ATTEMPTS = _env_int("AUTH_MAX_FAILED_ATTEMPTS", 5)
    AUTH_LOCKOUT_MINUTES = _env_int("AUTH_LOCKOUT_MINUTES", 15)

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov"}
    INSTANCE_DIR = DEFAULT_INSTANCE_DIR
    TEMP_UPLOAD_ROOT = DEFAULT_INSTANCE_DIR / "temp" / "inspections"

    IMAGEKIT_PUBLIC_KEY = os.getenv("IMAGEKIT_PUBLIC_KEY", "")
    IMAGEKIT_PRIVATE_KEY = os.getenv("IMAGEKIT_PRIVATE_KEY", "")
    IMAGEKIT_URL_ENDPOINT = os.getenv("IMAGEKIT_URL_ENDPOINT", "")

    YOLO_MODEL_PATH = os.getenv(
        "YOLO_MODEL_PATH",
        str(SYSTEM_DIR / "artifacts" / "ai_models" / "best.pt"),
    )
    YOLO_CONFIDENCE = _env_float("YOLO_CONFIDENCE", 0.25)
    YOLO_IOU = _env_float("YOLO_IOU", 0.45)
    YOLO_IMGSZ = _env_int("YOLO_IMGSZ", 640)
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
    YOLO_FORCE_MOCK = _env_bool("YOLO_FORCE_MOCK")
    ANALYSIS_MAX_WORKERS = _env_int("ANALYSIS_MAX_WORKERS", 2)

    SEGMENTATION_ENABLED = _env_bool("SEGMENTATION_ENABLED")
    SEGMENTATION_FORCE_MOCK = _env_bool("SEGMENTATION_FORCE_MOCK", "true")
    SEGMENTATION_MODEL_PATH = os.getenv("SEGMENTATION_MODEL_PATH", "") or ""
    SEGMENTATION_DEVICE = os.getenv("SEGMENTATION_DEVICE", "cpu")
    SEGMENTATION_CONFIDENCE_THRESHOLD = _env_float(
        "SEGMENTATION_CONFIDENCE_THRESHOLD", 0.25
    )
    SEGMENTATION_IMAGE_SIZE = _env_int("SEGMENTATION_IMAGE_SIZE", 640)
    SEGMENTATION_OUTPUT_DIR = os.getenv("SEGMENTATION_OUTPUT_DIR", "") or ""

    DEPTH_ENABLED = _env_bool("DEPTH_ENABLED")
    DEPTH_FORCE_MOCK = _env_bool("DEPTH_FORCE_MOCK", "true")
    DEPTH_MODEL_PATH = os.getenv("DEPTH_MODEL_PATH", "") or ""
    DEPTH_MODEL_TYPE = os.getenv("DEPTH_MODEL_TYPE", "relative_mock")
    DEPTH_DEVICE = os.getenv("DEPTH_DEVICE", "cpu")
    DEPTH_IMAGE_SIZE = _env_int("DEPTH_IMAGE_SIZE", 384)
    DEPTH_OUTPUT_DIR = os.getenv("DEPTH_OUTPUT_DIR", "") or ""
    DEPTH_NORMALIZATION_MODE = os.getenv("DEPTH_NORMALIZATION_MODE", "relative")
    DEPTH_MIN_CONFIDENCE = _env_float("DEPTH_MIN_CONFIDENCE", 0.0)

    # Camera pose assumed when converting boxes to real-world size (severity v2).
    CAMERA_HEIGHT_M = _env_float("CAMERA_HEIGHT_M", 1.4)
    CAMERA_PITCH_DEG = _env_float("CAMERA_PITCH_DEG", 30.0)
    CAMERA_HFOV_DEG = _env_float("CAMERA_HFOV_DEG", 66.0)
    CAMERA_MAX_RANGE_M = _env_float("CAMERA_MAX_RANGE_M", 20.0)
    CAMERA_VIDEO_HEIGHT_M = _env_float("CAMERA_VIDEO_HEIGHT_M", 1.3)
    CAMERA_VIDEO_PITCH_DEG = _env_float("CAMERA_VIDEO_PITCH_DEG", 12.0)

    SEVERITY_RULE_VERSION = os.getenv("SEVERITY_RULE_VERSION", "phase10-v2-geometry")
    SEVERITY_CONFIDENCE_MIN_REVIEW = _env_float("SEVERITY_CONFIDENCE_MIN_REVIEW", 0.35)
    SEVERITY_CONFIDENCE_AMBIGUOUS = _env_float("SEVERITY_CONFIDENCE_AMBIGUOUS", 0.45)
    # GPS trust: browser fixes coarser than REVIEW are flagged; coarser than
    # REJECT (city-level PC/IP estimates) are refused until the user pins the map.
    GPS_REVIEW_ACCURACY_M = _env_float("GPS_REVIEW_ACCURACY_M", 100.0)
    GPS_REJECT_ACCURACY_M = _env_float("GPS_REJECT_ACCURACY_M", 5000.0)

    VERIFICATION_SSIM_VERIFIED_MAX = _env_float("VERIFICATION_SSIM_VERIFIED_MAX", 0.60)
    VERIFICATION_SSIM_PARTIAL_MAX = _env_float("VERIFICATION_SSIM_PARTIAL_MAX", 0.85)

    REPORTS_OUTPUT_ROOT = Path(
        os.getenv(
            "REPORTS_OUTPUT_ROOT",
            str(DEFAULT_INSTANCE_DIR / "generated_reports"),
        )
    )

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR = Path(os.getenv("LOG_DIR", str(DEFAULT_INSTANCE_DIR / "logs")))
    AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", str(LOG_DIR)))
    LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 2_000_000)
    LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 5)

    RATELIMIT_ENABLED = _env_bool("RATELIMIT_ENABLED", "true")
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "100 per hour;20 per minute")
    SECURITY_HEADERS_ENABLED = _env_bool("SECURITY_HEADERS_ENABLED", "true")
    OFFLINE_MODE = _env_bool("OFFLINE_MODE")
