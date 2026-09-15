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

    from app.config import is_usable_database_url, load_project_env, resolve_offline_mode

    load_project_env()
    offline = resolve_offline_mode()
    os.environ["OFFLINE_MODE"] = "true" if offline else "false"

    from app import create_app

    application = create_app()
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    db_ok = is_usable_database_url(os.getenv("DATABASE_URL"))
    mode = "OFFLINE (no RDS login)" if offline else "ONLINE (RDS + ImageKit)"
    print(f"Starting Municipal Road Inspection System — {mode}")
    if not offline and not db_ok:
        print("WARNING: DATABASE_URL is missing or still a placeholder.")
    application.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
