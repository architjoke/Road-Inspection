"""Phase 14 structured logging with RotatingFileHandler (offline-safe)."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(app=None) -> Path | None:
    """
    Configure root + app loggers with console + rotating file handlers.

    Never contacts remote log aggregators. Safe to call multiple times.
    """
    global _CONFIGURED

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    if app is not None:
        log_level_name = str(app.config.get("LOG_LEVEL", log_level_name)).upper()
    level = getattr(logging, log_level_name, logging.INFO)

    if app is not None:
        from app.config import DEFAULT_INSTANCE_DIR

        log_dir = Path(app.config.get("LOG_DIR") or (DEFAULT_INSTANCE_DIR / "logs"))
        max_bytes = int(app.config.get("LOG_MAX_BYTES", 2_000_000))
        backup_count = int(app.config.get("LOG_BACKUP_COUNT", 5))
    else:
        from app.config import DEFAULT_INSTANCE_DIR

        log_dir = DEFAULT_INSTANCE_DIR / "logs"
        max_bytes = int(os.getenv("LOG_MAX_BYTES", "2000000"))
        backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s | %(levelname)s | %(name)s | "
            "%(message)s | pid=%(process)d"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers across create_app / reload
    if not _CONFIGURED:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        _CONFIGURED = True
    else:
        root.setLevel(level)

    if app is not None:
        app.logger.setLevel(level)
        app.logger.info(
            "Logging configured level=%s file=%s", log_level_name, log_file
        )

    return log_file


