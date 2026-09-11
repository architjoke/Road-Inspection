"""Development / offline launcher with local folder bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _bootstrap_dirs() -> None:
    for relative in (
        "app/instance/logs",
        "app/instance/generated_reports",
        "app/instance/temp/inspections",
    ):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)


def main() -> None:
    _bootstrap_dirs()

    # Default to offline when DATABASE_URL is unset / placeholder.
    if os.getenv("OFFLINE_MODE") is None:
        db_url = os.getenv("DATABASE_URL") or ""
        if (
            not db_url
            or "YOUR-RDS-HOST" in db_url
            or "USERNAME:PASSWORD" in db_url
        ):
            os.environ.setdefault("OFFLINE_MODE", "true")

    from app import create_app

    application = create_app()
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    application.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
