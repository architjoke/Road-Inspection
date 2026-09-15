"""WSGI entrypoint. Loads .env before deciding online vs offline."""

from __future__ import annotations

import os

from app.config import load_project_env, resolve_offline_mode

load_project_env()
os.environ["OFFLINE_MODE"] = "true" if resolve_offline_mode() else "false"

from app import create_app  # noqa: E402

app = create_app()
