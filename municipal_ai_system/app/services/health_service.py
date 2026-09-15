"""Offline health/status aggregation for /health (no remote DB pings)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def build_health_payload(app) -> dict[str, Any]:
    """Assemble component health without contacting RDS/Aurora/AWS."""
    from app.services.audit_trail import list_audit_events
    from app.services.comparison_service import list_comparisons
    from app.services.detection_service import list_memory_detections
    from app.services.offline_store import offline_store
    from app.workers import executor as analysis_executor
    from app.workers.task_store import task_store

    offline = bool(app.config.get("OFFLINE_MODE"))
    db_configured = bool(app.config.get("DATABASE_CONFIGURED"))

    # Storage paths
    temp_root = Path(app.config.get("TEMP_UPLOAD_ROOT", "tmp"))
    reports_root = Path(
        app.config.get(
            "REPORTS_OUTPUT_ROOT",
            Path(app.root_path) / "instance" / "generated_reports",
        )
    )
    log_dir = Path(
        app.config.get("LOG_DIR", Path(app.root_path) / "instance" / "logs")
    )

    def _dir_status(path: Path) -> dict[str, Any]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            writable = os.access(path, os.W_OK)
            return {
                "path": str(path),
                "exists": path.exists(),
                "writable": writable,
                "status": "ok" if path.exists() and writable else "degraded",
            }
        except Exception as exc:
            return {
                "path": str(path),
                "exists": False,
                "writable": False,
                "status": "error",
                "error": str(exc),
            }

    # Model load status (local path check only — no download)
    model_path = Path(
        app.config.get(
            "YOLO_MODEL_PATH",
            Path(app.root_path) / "ai_models" / "best.pt",
        )
    )
    force_mock = bool(app.config.get("YOLO_FORCE_MOCK"))
    model_status = {
        "force_mock": force_mock,
        "path": str(model_path),
        "weights_present": model_path.is_file(),
        "status": (
            "mock"
            if force_mock
            else ("ok" if model_path.is_file() else "missing_weights")
        ),
    }

    executor_info = {
        "max_workers": analysis_executor.executor_max_workers(),
        "status": "ok",
    }
    try:
        pool = analysis_executor.get_executor()
        executor_info["active"] = True
        # ThreadPoolExecutor does not expose active count portably; approximate
        unfinished = getattr(pool, "_work_queue", None)
        executor_info["queued_approx"] = (
            unfinished.qsize() if unfinished is not None else None
        )
    except Exception as exc:
        executor_info["active"] = False
        executor_info["status"] = "error"
        executor_info["error"] = str(exc)

    memory_store = {
        "inspections": len(offline_store.list_inspections()),
        "detections": len(list_memory_detections()),
        "comparisons": len(list_comparisons()),
        "tasks": task_store.count(),
        "audit_events_recent": len(list_audit_events(50)),
        "status": "ok",
    }

    from app.services.imagekit_service import is_imagekit_configured

    imagekit_ok = False
    try:
        imagekit_ok = is_imagekit_configured()
    except Exception:
        imagekit_ok = False

    components = {
        "offline_mode": {"enabled": offline, "status": "ok"},
        "imagekit": {
            "configured": imagekit_ok,
            "status": "ok" if imagekit_ok else ("skipped_offline" if offline else "unconfigured"),
        },
        "database": {
            "configured": db_configured,
            "contacted": False,
            "status": "skipped_offline" if offline or not db_configured else "configured",
            "note": "Health check never opens a remote DB connection.",
        },
        "memory_store": memory_store,
        "executor": executor_info,
        "model": model_status,
        "storage": {
            "temp_uploads": _dir_status(temp_root),
            "reports": _dir_status(reports_root),
            "logs": _dir_status(log_dir),
        },
        "security": {
            "csrf_enabled": bool(app.config.get("WTF_CSRF_ENABLED", True)),
            "rate_limit_enabled": bool(app.config.get("RATELIMIT_ENABLED", True)),
            "rate_limit_storage": app.config.get(
                "RATELIMIT_STORAGE_URI", "memory://"
            ),
        },
    }

    degraded = []
    for name, comp in components.items():
        if isinstance(comp, dict):
            if comp.get("status") in {"error", "missing_weights", "degraded"}:
                degraded.append(name)
            for nested in comp.values():
                if isinstance(nested, dict) and nested.get("status") in {
                    "error",
                    "degraded",
                }:
                    degraded.append(f"{name}.{nested.get('path', 'item')}")

    overall = "ok" if not degraded else "degraded"
    return {
        "status": overall,
        "application": "AI Road Inspection System",
        "phase": "phase_14_launch_readiness",
        "database_configured": db_configured,
        "offline_mode": offline,
        "rds_contacted": False,
        "components": components,
        "degraded": degraded,
    }
