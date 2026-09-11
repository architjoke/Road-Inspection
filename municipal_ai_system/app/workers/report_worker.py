"""Background report generation worker (Phase 13)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.report_service import write_report_file

logger = logging.getLogger(__name__)


def generate_inspection_report(
    inspection_id: int,
    fmt: str = "pdf",
) -> dict[str, Any]:
    """Compile and write an inspection report file offline."""
    logger.info(
        "Report worker start inspection=%s format=%s", inspection_id, fmt
    )
    result = write_report_file(inspection_id, fmt)
    logger.info(
        "Report worker done inspection=%s file=%s",
        inspection_id,
        result.get("filename"),
    )
    return result
