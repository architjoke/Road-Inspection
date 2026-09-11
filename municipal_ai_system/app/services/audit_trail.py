"""Offline file-based audit trail (no RDS required)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import has_request_context, request

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_memory_events: list[dict[str, Any]] = []
_MAX_MEMORY = 500


def clear_audit_events() -> None:
    with _lock:
        _memory_events.clear()


def list_audit_events(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        return list(_memory_events[-limit:])


def _audit_path() -> Path:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            root = Path(
                current_app.config.get(
                    "AUDIT_LOG_DIR",
                    Path(current_app.root_path) / "instance" / "logs",
                )
            )
            root.mkdir(parents=True, exist_ok=True)
            return root / "audit_trail.jsonl"
    except Exception:
        pass
    fallback = Path(__file__).resolve().parents[1] / "instance" / "logs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / "audit_trail.jsonl"


def record_audit_event(
    action: str,
    *,
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    """
    Append one audit event to memory + local JSONL file.

    Never contacts RDS/Aurora/AWS.
    """
    ip = None
    path = None
    method = None
    if has_request_context():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        path = request.path
        method = request.method

    event = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "actor": actor or "system",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "ip": ip,
        "path": path,
        "method": method,
        "details": details or {},
    }

    with _lock:
        _memory_events.append(event)
        if len(_memory_events) > _MAX_MEMORY:
            del _memory_events[: len(_memory_events) - _MAX_MEMORY]
        try:
            with _audit_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, default=str) + "\n")
        except Exception:
            logger.debug("Could not append audit JSONL", exc_info=True)

    logger.info(
        "AUDIT action=%s actor=%s resource=%s:%s status=%s",
        action,
        event["actor"],
        resource_type,
        resource_id,
        status,
    )
    return event
