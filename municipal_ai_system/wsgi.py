"""WSGI entrypoint for production-style local launch (no remote services required)."""

from __future__ import annotations

import os

# Prefer offline mode when launching without an explicit DATABASE_URL.
if os.getenv("OFFLINE_MODE") is None and not os.getenv("DATABASE_URL"):
    os.environ.setdefault("OFFLINE_MODE", "true")

from app import create_app  # noqa: E402

app = create_app()
