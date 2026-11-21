"""Dashboard analytics aggregates for Chart.js widgets."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, text

from app.extensions import db
from app.models import get_model
from app.models.inspection import Inspection
from app.models.ward import Ward
from app.utils.status_constants import REPAIR_VERIFIED, CLOSED



def get_analytics_summary(municipality_id: int | None = None) -> dict:
    """Return card + chart payloads for admin dashboards."""
    from flask import current_app, has_app_context

    offline = False
    if has_app_context():
        offline = bool(
            current_app.config.get("TESTING")
            or current_app.config.get("OFFLINE_MODE")
            or not current_app.config.get("DATABASE_CONFIGURED")
        )
    if offline:
        return _offline_analytics(municipality_id)
    try:
        return _db_analytics(municipality_id)
    except Exception:
        return _offline_analytics(municipality_id)


def _offline_analytics(municipality_id: int | None = None) -> dict:
    from app.services.detection_service import list_detections_merged
    from app.services.offline_store import offline_store
    from app.services.repair_service import repair_stats

    inspections = offline_store.list_inspections()
    if municipality_id is not None:
        inspections = [
            item for item in inspections if item.municipality_id == municipality_id
        ]
    detections = list_detections_merged()
    severity_by_label = {"SEVERE": 0, "MODERATE": 0, "MINOR": 0}
    damage_by_type: dict[str, int] = {}
    scores = []
    for det in detections:
        sev = (det.get("severity") or "").upper()
        if sev in severity_by_label:
            severity_by_label[sev] += 1
        label = (det.get("damage_type") or det.get("class_name") or "unknown")
        label = str(label).replace("_", " ").title()
        damage_by_type[label] = damage_by_type.get(label, 0) + 1
        if det.get("severity_score") is not None:
            try:
                scores.append(float(det["severity_score"]))
            except (TypeError, ValueError):
                pass
    if not damage_by_type:
        damage_by_type = {
            "Pothole": 0,
            "Longitudinal Crack": 0,
            "Lateral Crack": 0,
            "Alligator Crack": 0,
        }
    stats = repair_stats()
    processing = len(
        [
            i
            for i in inspections
            if str(getattr(i, "status", "") or "").lower()
            in {"processing", "pending_analysis", "uploaded", "pending"}
        ]
    )
    return {
        "cards": {
            "total_inspections": len(inspections),
            "total_detections": len(detections),
            "processing": processing,
            "avg_severity_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "inspections_last_30_days": len(inspections),
            "repairs_completed_this_month": stats.get("completed", 0),
        },
        "severity_bar": {
            "labels": list(severity_by_label.keys()),
            "values": list(severity_by_label.values()),
        },
        "damage_pie": {
            "labels": list(damage_by_type.keys()),
            "values": list(damage_by_type.values()),
        },
        "inspections_per_week": {
            "labels": [f"W{i}" for i in range(1, 13)],
            "values": [0] * 12,
        },
        "ward_breakdown": [],
    }


def _db_analytics(municipality_id: int | None = None) -> dict:
    """Return card + chart payloads for admin dashboards."""
    now = datetime.utcnow()
    day_30 = now - timedelta(days=30)
    week_12 = now - timedelta(weeks=12)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    inspection_query = Inspection.query
    if municipality_id is not None:
        inspection_query = inspection_query.filter(
            Inspection.municipality_id == municipality_id
        )

    total_inspections = inspection_query.count()

    inspections_30 = inspection_query.filter(
        Inspection.inspection_date >= day_30
    ).count()

    repairs_completed = inspection_query.filter(
        Inspection.status.in_([REPAIR_VERIFIED, CLOSED]),
        Inspection.updated_at >= month_start,
    ).count()

    # Weekly trend (last 12 weeks) from inspections
    week_rows = (
        db.session.query(
            func.date_trunc("week", Inspection.inspection_date).label("week"),
            func.count(Inspection.id),
        )
        .filter(Inspection.inspection_date >= week_12)
    )
    if municipality_id is not None:
        week_rows = week_rows.filter(Inspection.municipality_id == municipality_id)
    week_rows = week_rows.group_by("week").order_by("week").all()

    inspections_per_week = {
        "labels": [
            (row[0].date().isoformat() if row[0] else "")
            for row in week_rows
        ],
        "values": [int(row[1]) for row in week_rows],
    }

    ward_breakdown = (
        db.session.query(Ward.name, func.count(Inspection.id))
        .outerjoin(Inspection, Inspection.ward_id == Ward.id)
    )
    if municipality_id is not None:
        ward_breakdown = ward_breakdown.filter(
            Ward.municipality_id == municipality_id
        )
    ward_breakdown = (
        ward_breakdown.group_by(Ward.name)
        .order_by(func.count(Inspection.id).desc())
        .limit(12)
        .all()
    )

    severity_by_label = {"SEVERE": 0, "MODERATE": 0, "MINOR": 0}
    damage_by_type: dict[str, int] = {}
    total_detections = 0
    avg_severity_score = 0.0

    Detection = get_model("detections")
    if Detection is not None and hasattr(Detection, "__table__"):
        columns = {c.name for c in Detection.__table__.columns}
        severity_col = next(
            (
                name
                for name in ("severity", "severity_level", "severity_label")
                if name in columns
            ),
            None,
        )
        damage_col = next(
            (
                name
                for name in (
                    "damage_type",
                    "defect_type",
                    "defect_class",
                    "class_name",
                )
                if name in columns
            ),
            None,
        )

        try:
            total_detections = db.session.query(
                func.count()
            ).select_from(Detection.__table__).scalar() or 0

            if severity_col:
                rows = db.session.execute(
                    text(
                        f"""
                        SELECT UPPER(CAST({severity_col} AS TEXT)) AS sev, COUNT(*)
                        FROM detections
                        WHERE created_at >= :since
                           OR created_at IS NULL
                        GROUP BY sev
                        """
                    ),
                    {"since": day_30},
                ).all()
                for sev, count in rows:
                    key = (sev or "UNKNOWN").upper()
                    if key in severity_by_label:
                        severity_by_label[key] = int(count)

                score_map = {"MINOR": 1, "MODERATE": 2, "SEVERE": 3}
                weighted = sum(
                    severity_by_label[k] * score_map[k] for k in score_map
                )
                total_sev = sum(severity_by_label.values()) or 1
                avg_severity_score = round(weighted / total_sev, 2)

            if damage_col:
                rows = db.session.execute(
                    text(
                        f"""
                        SELECT CAST({damage_col} AS TEXT) AS dtype, COUNT(*)
                        FROM detections
                        GROUP BY dtype
                        ORDER BY COUNT(*) DESC
                        LIMIT 12
                        """
                    )
                ).all()
                for dtype, count in rows:
                    label = (dtype or "unknown").replace("_", " ").title()
                    damage_by_type[label] = int(count)
        except Exception:
            # Table reflected but columns/data not ready yet.
            total_detections = 0

    if not any(severity_by_label.values()):
        severity_by_label = {"SEVERE": 0, "MODERATE": 0, "MINOR": 0}

    # Prefer live/memory detections when DB table is empty so dashboards stay current.
    try:
        from app.services.detection_service import list_detections_merged

        merged = list_detections_merged()
        if merged and int(total_detections or 0) < len(merged):
            total_detections = len(merged)
            for det in merged:
                sev = (det.get("severity") or "").upper()
                if sev in severity_by_label:
                    severity_by_label[sev] += 1
                label = (det.get("damage_type") or det.get("class_name") or "unknown")
                label = str(label).replace("_", " ").title()
                damage_by_type[label] = damage_by_type.get(label, 0) + 1
    except Exception:
        pass

    if not damage_by_type:
        damage_by_type = {
            "Pothole": 0,
            "Longitudinal Crack": 0,
            "Lateral Crack": 0,
            "Alligator Crack": 0,
        }

    return {
        "cards": {
            "total_inspections": total_inspections,
            "inspections_last_30_days": inspections_30,
            "total_detections": int(total_detections),
            "avg_severity_score": avg_severity_score,
            "repairs_completed_this_month": repairs_completed,
        },
        "severity_bar": {
            "labels": list(severity_by_label.keys()),
            "values": list(severity_by_label.values()),
        },
        "damage_pie": {
            "labels": list(damage_by_type.keys()),
            "values": list(damage_by_type.values()),
        },
        "inspections_per_week": inspections_per_week,
        "ward_breakdown": [
            {"ward_name": name or "Unassigned", "count": int(count)}
            for name, count in ward_breakdown
        ],
    }
