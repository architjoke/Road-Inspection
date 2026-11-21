"""Who may see which inspection (and everything hanging off it).

Single rule used by pages, APIs, detections and reports:

* super admin      -> every inspection
* field worker     -> inspections they uploaded
* contractor       -> inspections with a repair job assigned to them
* everyone else    -> inspections of their own municipality
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from flask_login import current_user

logger = logging.getLogger(__name__)


def _flag(user, name: str) -> bool:
    value = getattr(user, name, False)
    try:
        return bool(value()) if callable(value) else bool(value)
    except Exception:
        return False


def _user_pk(user) -> int | None:
    for attr in ("pk", "id"):
        value = getattr(user, attr, None)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def can_view_inspection(inspection, user=None) -> bool:
    user = user or current_user
    if inspection is None:
        return False
    if _flag(user, "is_super_admin"):
        return True
    if _flag(user, "is_field_worker"):
        return getattr(inspection, "user_id", None) == _user_pk(user)
    if _flag(user, "is_contractor"):
        from app.services.repair_service import list_repairs

        return any(
            row.get("inspection_id") == getattr(inspection, "id", None)
            for row in list_repairs(assignee_id=_user_pk(user))
        )
    return getattr(inspection, "municipality_id", None) == getattr(user, "municipality_id", None)


def load_inspection(inspection_id: int):
    from app.services.offline_store import offline_mode_enabled, offline_store

    inspection = None
    if not offline_mode_enabled():
        try:
            from app.extensions import db
            from app.models.inspection import Inspection

            inspection = db.session.get(Inspection, int(inspection_id))
        except Exception:
            logger.exception("Inspection lookup failed for %s", inspection_id)
            try:
                from app.extensions import db

                db.session.rollback()
            except Exception:
                pass
    if inspection is None:
        inspection = offline_store.get_inspection(int(inspection_id))
    return inspection


def can_view_inspection_id(inspection_id: Any, user=None) -> bool:
    """True when the inspection exists and the user may see it."""
    try:
        inspection = load_inspection(int(inspection_id))
    except (TypeError, ValueError):
        return False
    return can_view_inspection(inspection, user)


def filter_rows_by_inspection(rows: Iterable[dict[str, Any]], user=None) -> list[dict[str, Any]]:
    """Keep rows (detections, repairs, ...) whose inspection the user may see."""
    from app.services.offline_store import offline_mode_enabled

    user = user or current_user
    rows = list(rows)
    if _flag(user, "is_super_admin"):
        return rows
    verdict: dict[Any, bool] = {}
    out = []
    for row in rows:
        insp_id = row.get("inspection_id")
        if insp_id not in verdict:
            inspection = None
            try:
                inspection = load_inspection(int(insp_id)) if insp_id is not None else None
            except (TypeError, ValueError):
                inspection = None
            if inspection is None:
                # Offline/dev data without an inspection record stays visible;
                # with a real database an orphan row is hidden.
                verdict[insp_id] = offline_mode_enabled()
            else:
                verdict[insp_id] = can_view_inspection(inspection, user)
        if verdict[insp_id]:
            out.append(row)
    return out
