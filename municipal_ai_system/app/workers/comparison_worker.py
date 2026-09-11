"""Background helper for before/after verification (reuses sync service)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.comparison_service import run_verification

logger = logging.getLogger(__name__)


def compare_repair_images(
    before_path: str | Path,
    after_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Worker-friendly wrapper around comparison_service.run_verification."""
    logger.info("Comparison worker start before=%s after=%s", before_path, after_path)
    return run_verification(before_path, after_path, **kwargs)
