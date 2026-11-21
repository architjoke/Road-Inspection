"""Runtime data locations (logs, uploads, reports, caches).

Everything the running app writes lives under ``INSTANCE_DIR``
(default ``<project root>/instance/``, override with the ``INSTANCE_DIR``
environment variable) — never inside the ``app`` code package.
"""

from __future__ import annotations

from pathlib import Path

from app.config import DEFAULT_INSTANCE_DIR


def instance_dir(*parts: str) -> Path:
    """``INSTANCE_DIR/<parts>``, created on demand."""
    base = DEFAULT_INSTANCE_DIR
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("INSTANCE_DIR"):
            base = Path(current_app.config["INSTANCE_DIR"])
    except Exception:
        pass
    path = Path(base).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
