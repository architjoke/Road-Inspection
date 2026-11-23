"""Phase 13 multi-format report compilation and export (offline-safe)."""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

REPORT_TITLE = "Municipal Road Inspection Report"
RULE_VERSION = "phase13-v1"

_TIMELINE = {
    "SEVERE": {
        "window": "24-hour emergency response",
        "hours": 24,
        "label": "Emergency",
    },
    "MODERATE": {
        "window": "7-day priority repair",
        "hours": 168,
        "label": "Priority",
    },
    "MINOR": {
        "window": "30-day scheduled monitoring",
        "hours": 720,
        "label": "Scheduled",
    },
}

_lock = threading.RLock()
_report_jobs: dict[str, dict[str, Any]] = {}


def clear_report_jobs() -> None:
    with _lock:
        _report_jobs.clear()


def get_report_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        item = _report_jobs.get(job_id)
        return deepcopy(item) if item else None



def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _size_text(finding: dict[str, Any]) -> str:
    """Short human size label for tables ('dia 0.62 m', '2.4 m', '1.30 m2')."""
    if finding.get("size_basis") != "ground_plane_estimate":
        return "n/a"
    damage = str(finding.get("damage_type") or "").lower()
    if "pothole" in damage and finding.get("est_diameter_mm"):
        return f"dia {float(finding['est_diameter_mm']) / 1000.0:.2f} m"
    if "crack" in damage and "alligator" not in damage and finding.get("est_crack_length_m"):
        return f"{float(finding['est_crack_length_m']):.1f} m"
    if finding.get("est_area_m2"):
        return f"{float(finding['est_area_m2']):.2f} m2"
    return "n/a"


def estimate_repair_cost(
    damage_type: str | None,
    severity: str | None,
    measurements: dict[str, Any] | None = None,
) -> float:
    """Quantity x unit-rate cost (typical-size fallback when size is unknown)."""
    from app.ai.cost_engine import estimate_detection_cost
    from app.ai.severity_rules import classify_damage_family

    family = classify_damage_family(damage_type)
    return float(
        estimate_detection_cost(family, (severity or "MODERATE").upper(), measurements)["amount"]
    )


def reports_output_root() -> Path:
    try:
        from app.utils.paths import instance_dir

        root = Path(current_app.config.get("REPORTS_OUTPUT_ROOT") or instance_dir("generated_reports"))
    except Exception:
        from app.utils.paths import instance_dir

        root = instance_dir("generated_reports")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _inspection_as_dict(inspection: Any) -> dict[str, Any]:
    if inspection is None:
        return {}
    if isinstance(inspection, dict):
        return inspection
    media_items = []
    for m in getattr(inspection, "media_items", None) or []:
        if isinstance(m, dict):
            media_items.append(m)
        else:
            media_items.append(
                {
                    "id": getattr(m, "id", None),
                    "file_name": getattr(m, "file_name", None),
                    "file_path": getattr(m, "file_path", None),
                    "cdn_url": getattr(m, "cdn_url", None),
                    "thumbnail_path": getattr(m, "thumbnail_path", None),
                    "media_type": getattr(m, "media_type", None),
                    "is_before_image": getattr(m, "is_before_image", None),
                    "is_after_image": getattr(m, "is_after_image", None),
                }
            )
    created = getattr(inspection, "created_at", None) or getattr(
        inspection, "inspection_date", None
    )
    if isinstance(created, datetime):
        created = created.isoformat() + "Z"
    return {
        "id": getattr(inspection, "id", None),
        "inspection_code": getattr(inspection, "inspection_code", None),
        "municipality_id": getattr(inspection, "municipality_id", None),
        "user_id": getattr(inspection, "user_id", None),
        "ward_id": getattr(inspection, "ward_id", None),
        "location_name": getattr(inspection, "location_name", None),
        "location_latitude": getattr(inspection, "location_latitude", None),
        "location_longitude": getattr(inspection, "location_longitude", None),
        "status": getattr(inspection, "status", None),
        "inspection_type": getattr(inspection, "inspection_type", None),
        "notes": getattr(inspection, "notes", None),
        "total_detections": getattr(inspection, "total_detections", 0),
        "created_at": created,
        "media_items": media_items,
    }


def load_inspection_record(inspection_id: int) -> dict[str, Any] | None:
    """Load inspection from offline store first; optional reflected DB second."""
    from app.services.offline_store import offline_mode_enabled, offline_store

    offline = offline_store.get_inspection(inspection_id)
    if offline is not None:
        return _inspection_as_dict(offline)

    if offline_mode_enabled():
        return None

    try:
        from app.extensions import db
        from app.models.inspection import Inspection

        row = db.session.get(Inspection, inspection_id)
        if row is None:
            return None
        return _inspection_as_dict(row)
    except Exception:
        logger.debug("DB inspection load skipped", exc_info=True)
        return None


def compile_inspection_report(inspection_id: int) -> dict[str, Any]:
    """
    Build a complete offline report payload from Phases 6–12 data sources.
    """
    from app.services.comparison_service import list_comparisons
    from app.services.detection_review_service import list_detections_for_review

    inspection = load_inspection_record(inspection_id)
    if inspection is None:
        raise LookupError(f"Inspection {inspection_id} not found")

    lat = _safe_float(inspection.get("location_latitude"))
    lng = _safe_float(inspection.get("location_longitude"))

    det_payload = list_detections_for_review(inspection_id)
    detections_raw = det_payload.get("detections") or []

    findings: list[dict[str, Any]] = []
    severity_counts = {"MINOR": 0, "MODERATE": 0, "SEVERE": 0}
    priority_counts = {"1": 0, "2": 0, "3": 0}
    cost_inputs: list[dict[str, Any]] = []

    for det in detections_raw:
        severity = str(det.get("severity") or "MODERATE").upper()
        if severity not in severity_counts:
            severity = "MODERATE"
        priority = str(det.get("priority") or 2)
        if priority not in priority_counts:
            priority = "2"
        cost_detail = det.get("cost_estimate") or {}
        if det.get("estimated_cost") is not None:
            cost = float(det["estimated_cost"])
        else:
            cost = estimate_repair_cost(
                det.get("damage_type"), severity, det.get("measurements")
            )
            cost_detail = {}
        cost_inputs.append(
            {
                "cost_estimate": cost_detail,
                "estimated_cost": cost,
                "confidence": det.get("confidence"),
                "status": det.get("status"),
            }
        )
        severity_counts[severity] += 1
        priority_counts[priority] += 1
        timeline = _TIMELINE.get(severity, _TIMELINE["MODERATE"])
        findings.append(
            {
                "detection_id": det.get("detection_id"),
                "damage_type": det.get("damage_type") or det.get("class_name"),
                "latitude": lat,
                "longitude": lng,
                "severity": severity,
                "priority": int(priority) if str(priority).isdigit() else priority,
                "recommended_action": det.get("recommended_action"),
                "repair_type": det.get("repair_type"),
                "estimated_cost": cost,
                "currency": "INR",
                "cost_quantity": cost_detail.get("quantity"),
                "cost_unit": cost_detail.get("unit"),
                "cost_unit_rate": cost_detail.get("unit_rate"),
                "cost_basis": cost_detail.get("basis") or "typical_size_fallback",
                "cost_detail": cost_detail.get("detail"),
                "size_basis": det.get("size_basis"),
                "est_width_m": det.get("width_m"),
                "est_length_m": det.get("length_m"),
                "est_area_m2": det.get("area_sq_meters"),
                "est_diameter_mm": det.get("diameter_mm"),
                "est_crack_length_m": det.get("crack_length_m"),
                "est_depth_mm": det.get("estimated_depth_mm"),
                "manual_review_required": bool(det.get("manual_review_required")),
                "confidence": det.get("confidence"),
                "action_timeline": timeline["window"],
                "review_status": (
                    "manual_review"
                    if det.get("manual_review_required")
                    else (det.get("status") or "auto")
                ),
            }
        )

    from app.ai.cost_engine import summarize_costs

    cost_summary = summarize_costs(cost_inputs)
    comparisons = list_comparisons(inspection_id=inspection_id)
    verification_rows = [
        {
            "comparison_id": c.get("comparison_id"),
            "ssim": c.get("ssim"),
            "status": c.get("status"),
            "completion_percentage": c.get("completion_percentage"),
            "alignment_success": c.get("alignment_success"),
            "alignment_method": c.get("alignment_method"),
            "manual_review_required": c.get("manual_review_required"),
            "created_at": c.get("created_at"),
        }
        for c in comparisons
    ]
    avg_completion = None
    if verification_rows:
        avg_completion = round(
            sum(float(v.get("completion_percentage") or 0) for v in verification_rows)
            / len(verification_rows),
            2,
        )

    recommendations: list[dict[str, Any]] = []
    for severity in ("SEVERE", "MODERATE", "MINOR"):
        count = severity_counts[severity]
        if count <= 0:
            continue
        timeline = _TIMELINE[severity]
        recommendations.append(
            {
                "severity": severity,
                "count": count,
                "timeline": timeline["window"],
                "label": timeline["label"],
                "priority": {"SEVERE": 1, "MODERATE": 2, "MINOR": 3}[severity],
                "guidance": (
                    f"{count} {severity.lower()} finding(s) require "
                    f"{timeline['window']}."
                ),
            }
        )

    generated_at = datetime.utcnow().isoformat() + "Z"
    report = {
        "report_title": REPORT_TITLE,
        "rule_version": RULE_VERSION,
        "generated_at": generated_at,
        "offline": True,
        "header": {
            "inspection_id": inspection.get("id"),
            "inspection_code": inspection.get("inspection_code"),
            "timestamp": inspection.get("created_at") or generated_at,
            "ward_id": inspection.get("ward_id"),
            "ward_name": (
                f"Ward {inspection['ward_id']}"
                if inspection.get("ward_id") is not None
                else "Unassigned"
            ),
            "municipality_id": inspection.get("municipality_id"),
            "inspector_user_id": inspection.get("user_id"),
            "inspector_details": {
                "user_id": inspection.get("user_id"),
                "source": "offline_store",
            },
            "location_name": inspection.get("location_name"),
            "latitude": lat,
            "longitude": lng,
            "status": inspection.get("status"),
            "inspection_type": inspection.get("inspection_type"),
            "notes": inspection.get("notes"),
        },
        "executive_summary": {
            "total_detections": len(findings),
            "severity_breakdown": severity_counts,
            "priority_breakdown": {
                "priority_1": priority_counts["1"],
                "priority_2": priority_counts["2"],
                "priority_3": priority_counts["3"],
            },
            "estimated_total_repair_cost": cost_summary["total"],
            "cost_breakdown": cost_summary,
            "currency": "INR",
            "overall_status": inspection.get("status") or "unknown",
            "verification_count": len(verification_rows),
            "verification_avg_completion_percentage": avg_completion,
            "manual_review_findings": sum(
                1 for f in findings if f.get("manual_review_required")
            ),
        },
        "detailed_findings": findings,
        "verification": {
            "comparisons": verification_rows,
            "avg_completion_percentage": avg_completion,
            "count": len(verification_rows),
        },
        "recommendations": recommendations,
        "media": inspection.get("media_items") or [],
        "notes": {
            "cost_basis": (
                "Quantity x unit rate: pothole patch volume (m3), crack sealing "
                "length (m), alligator treatment area (m2). Sizes are estimated "
                "from the photo with an assumed camera pose; depth is assumed per "
                "severity band. Total adds contingency and one mobilisation charge. "
                "Detections below the manual-review confidence are excluded until a "
                f"reviewer approves them ({cost_summary.get('pending_review_count', 0)} pending, "
                f"INR {cost_summary.get('pending_review_items_total', 0):,.0f}); rejected ones are dropped. "
                f"Rates: {cost_summary.get('rate_source')} (not a municipal bid)."
            ),
            "rds_contacted": False,
        },
    }
    return report


def export_json_bytes(report: dict[str, Any]) -> bytes:
    return json.dumps(report, indent=2, default=str).encode("utf-8")


def _findings_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    header = report.get("header") or {}
    rows = []
    for f in report.get("detailed_findings") or []:
        rows.append(
            {
                "inspection_id": header.get("inspection_id"),
                "inspection_code": header.get("inspection_code"),
                "detection_id": f.get("detection_id"),
                "latitude": f.get("latitude"),
                "longitude": f.get("longitude"),
                "damage_type": f.get("damage_type"),
                "severity": f.get("severity"),
                "priority": f.get("priority"),
                "repair_type": f.get("repair_type"),
                "recommended_action": f.get("recommended_action"),
                "estimated_size": _size_text(f),
                "est_area_m2": f.get("est_area_m2"),
                "cost_quantity": f.get("cost_quantity"),
                "cost_unit": f.get("cost_unit"),
                "cost_unit_rate": f.get("cost_unit_rate"),
                "cost_basis": f.get("cost_basis"),
                "estimated_cost": f.get("estimated_cost"),
                "currency": f.get("currency"),
                "review_status": f.get("review_status"),
                "action_timeline": f.get("action_timeline"),
                "manual_review_required": f.get("manual_review_required"),
            }
        )
    # Always include at least a header-only row when empty for CSV consumers
    if not rows:
        rows.append(
            {
                "inspection_id": header.get("inspection_id"),
                "inspection_code": header.get("inspection_code"),
                "detection_id": None,
                "latitude": header.get("latitude"),
                "longitude": header.get("longitude"),
                "damage_type": None,
                "severity": None,
                "priority": None,
                "repair_type": None,
                "recommended_action": None,
                "estimated_size": None,
                "est_area_m2": None,
                "cost_quantity": None,
                "cost_unit": None,
                "cost_unit_rate": None,
                "cost_basis": None,
                "estimated_cost": 0,
                "currency": "INR",
                "review_status": "no_detections",
                "action_timeline": None,
                "manual_review_required": False,
            }
        )
    return rows


def export_csv_bytes(report: dict[str, Any]) -> bytes:
    rows = _findings_rows(report)
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    # Append verification summary section as comment-like extra rows via second block
    ver = report.get("verification") or {}
    if ver.get("comparisons"):
        buf.write("\n")
        buf.write("verification_comparison_id,ssim,status,completion_percentage,alignment_method\n")
        for c in ver["comparisons"]:
            buf.write(
                f"{c.get('comparison_id')},{c.get('ssim')},{c.get('status')},"
                f"{c.get('completion_percentage')},{c.get('alignment_method')}\n"
            )
    return buf.getvalue().encode("utf-8-sig")


def export_excel_bytes(report: dict[str, Any]) -> bytes:
    """Excel workbook via openpyxl; pandas used when available for DataFrame path."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed") from exc

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    header = report.get("header") or {}
    exec_sum = report.get("executive_summary") or {}
    summary_rows = [
        ("Report Title", report.get("report_title")),
        ("Generated At", report.get("generated_at")),
        ("Inspection ID", header.get("inspection_id")),
        ("Inspection Code", header.get("inspection_code")),
        ("Ward", header.get("ward_name")),
        ("Location", header.get("location_name")),
        ("Latitude", header.get("latitude")),
        ("Longitude", header.get("longitude")),
        ("Status", header.get("status")),
        ("Total Detections", exec_sum.get("total_detections")),
        ("Estimated Total Cost (INR)", exec_sum.get("estimated_total_repair_cost")),
        ("Severity MINOR", (exec_sum.get("severity_breakdown") or {}).get("MINOR")),
        ("Severity MODERATE", (exec_sum.get("severity_breakdown") or {}).get("MODERATE")),
        ("Severity SEVERE", (exec_sum.get("severity_breakdown") or {}).get("SEVERE")),
        ("Priority 1", (exec_sum.get("priority_breakdown") or {}).get("priority_1")),
        ("Priority 2", (exec_sum.get("priority_breakdown") or {}).get("priority_2")),
        ("Priority 3", (exec_sum.get("priority_breakdown") or {}).get("priority_3")),
    ]
    for idx, (k, v) in enumerate(summary_rows, start=1):
        summary.cell(row=idx, column=1, value=k)
        summary.cell(row=idx, column=2, value=v)

    findings_ws = wb.create_sheet("Findings")
    rows = _findings_rows(report)
    headers = list(rows[0].keys())
    for col, name in enumerate(headers, start=1):
        findings_ws.cell(row=1, column=col, value=name)
    for r_idx, row in enumerate(rows, start=2):
        for c_idx, name in enumerate(headers, start=1):
            findings_ws.cell(row=r_idx, column=c_idx, value=row.get(name))

    ver_ws = wb.create_sheet("Verification")
    ver_headers = [
        "comparison_id",
        "ssim",
        "status",
        "completion_percentage",
        "alignment_success",
        "alignment_method",
        "manual_review_required",
    ]
    for col, name in enumerate(ver_headers, start=1):
        ver_ws.cell(row=1, column=col, value=name)
    for r_idx, c in enumerate((report.get("verification") or {}).get("comparisons") or [], start=2):
        for c_idx, name in enumerate(ver_headers, start=1):
            ver_ws.cell(row=r_idx, column=c_idx, value=c.get(name))

    rec_ws = wb.create_sheet("Recommendations")
    rec_headers = ["severity", "count", "priority", "timeline", "guidance"]
    for col, name in enumerate(rec_headers, start=1):
        rec_ws.cell(row=1, column=col, value=name)
    for r_idx, rec in enumerate(report.get("recommendations") or [], start=2):
        for c_idx, name in enumerate(rec_headers, start=1):
            rec_ws.cell(row=r_idx, column=c_idx, value=rec.get(name))

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def export_pdf_bytes(report: dict[str, Any]) -> bytes:
    """Multi-page PDF via ReportLab (no remote fonts/assets)."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=report.get("report_title") or REPORT_TITLE,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=16,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "SectionHead",
        parent=styles["Heading2"],
        fontSize=12,
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "BodyTextCustom",
        parent=styles["Normal"],
        fontSize=9,
        alignment=TA_LEFT,
        leading=12,
    )

    header = report.get("header") or {}
    exec_sum = report.get("executive_summary") or {}
    story: list[Any] = []
    story.append(Paragraph(report.get("report_title") or REPORT_TITLE, title_style))
    story.append(
        Paragraph(
            f"Generated: {report.get('generated_at')} · Offline export · "
            f"Rule {report.get('rule_version')}",
            body,
        )
    )
    story.append(Spacer(1, 6))

    story.append(Paragraph("1. Header & Metadata", h2))
    meta = [
        ["Inspection ID", str(header.get("inspection_id"))],
        ["Inspection Code", str(header.get("inspection_code") or "—")],
        ["Timestamp", str(header.get("timestamp") or "—")],
        ["Ward", str(header.get("ward_name") or "—")],
        ["Inspector User ID", str(header.get("inspector_user_id") or "—")],
        ["Location", str(header.get("location_name") or "—")],
        [
            "GPS",
            f"{header.get('latitude')}, {header.get('longitude')}"
            if header.get("latitude") is not None
            else "—",
        ],
        ["Status", str(header.get("status") or "—")],
    ]
    meta_table = Table(meta, colWidths=[55 * mm, 110 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.92, 0.94, 0.96)),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(meta_table)

    story.append(Paragraph("2. Executive Summary", h2))
    sev = exec_sum.get("severity_breakdown") or {}
    pri = exec_sum.get("priority_breakdown") or {}
    summary_data = [
        ["Metric", "Value"],
        ["Total detections", str(exec_sum.get("total_detections", 0))],
        ["SEVERE / MODERATE / MINOR", f"{sev.get('SEVERE', 0)} / {sev.get('MODERATE', 0)} / {sev.get('MINOR', 0)}"],
        [
            "Priority 1 / 2 / 3",
            f"{pri.get('priority_1', 0)} / {pri.get('priority_2', 0)} / {pri.get('priority_3', 0)}",
        ],
        [
            "Estimated repair cost (INR)",
            f"{exec_sum.get('estimated_total_repair_cost', 0):,.2f}",
        ],
        [
            "  items / contingency / mobilisation",
            "{:,.0f} / {:,.0f} / {:,.0f}".format(
                (exec_sum.get("cost_breakdown") or {}).get("items_total", 0),
                (exec_sum.get("cost_breakdown") or {}).get("contingency", 0),
                (exec_sum.get("cost_breakdown") or {}).get("mobilisation", 0),
            ),
        ],
        ["Overall status", str(exec_sum.get("overall_status") or "—")],
        [
            "Verification avg completion %",
            str(exec_sum.get("verification_avg_completion_percentage") or "—"),
        ],
    ]
    sum_table = Table(summary_data, colWidths=[75 * mm, 90 * mm])
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.15, 0.35, 0.55)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.Color(0.97, 0.97, 0.98)),
            ]
        )
    )
    story.append(sum_table)

    story.append(Paragraph("3. Detailed Findings", h2))
    findings = report.get("detailed_findings") or []
    find_header = [
        "ID",
        "Damage",
        "Sev",
        "Pri",
        "Est. size",
        "Repair",
        "Cost",
        "Review",
    ]
    find_data = [find_header]
    if not findings:
        find_data.append(["—", "No detections", "—", "—", "—", "—", "0", "n/a"])
    for f in findings[:40]:
        find_data.append(
            [
                str(f.get("detection_id") or "—"),
                str(f.get("damage_type") or "—")[:18],
                str(f.get("severity") or "—")[:8],
                str(f.get("priority") or "—"),
                _size_text(f),
                str(f.get("repair_type") or "—")[:22],
                f"{float(f.get('estimated_cost') or 0):,.0f}",
                "review" if f.get("manual_review_required") else "ok",
            ]
        )
    find_table = Table(
        find_data,
        colWidths=[12 * mm, 28 * mm, 16 * mm, 10 * mm, 22 * mm, 42 * mm, 20 * mm, 16 * mm],
    )
    find_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.45, 0.35)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(find_table)
    if len(findings) > 40:
        story.append(
            Paragraph(f"… and {len(findings) - 40} additional finding(s).", body)
        )

    story.append(Paragraph("4. Verification & Before/After", h2))
    ver = report.get("verification") or {}
    comps = ver.get("comparisons") or []
    if not comps:
        story.append(Paragraph("No before/after verification records for this inspection.", body))
    else:
        vdata = [["Comparison", "SSIM", "Status", "Completion %", "Alignment"]]
        for c in comps[:20]:
            vdata.append(
                [
                    str(c.get("comparison_id") or "")[:12],
                    str(c.get("ssim")),
                    str(c.get("status") or "")[:28],
                    str(c.get("completion_percentage")),
                    str(c.get("alignment_method") or "—"),
                ]
            )
        vtable = Table(vdata, colWidths=[28 * mm, 22 * mm, 55 * mm, 28 * mm, 32 * mm])
        vtable.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.45, 0.3, 0.15)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(vtable)

    # Embed first available local photo thumbnail if present
    for media in report.get("media") or []:
        path = media.get("thumbnail_path") or media.get("file_path")
        if path and Path(path).is_file():
            try:
                story.append(Paragraph("Field photo (local)", h2))
                img = Image(path, width=90 * mm, height=60 * mm)
                story.append(img)
            except Exception:
                logger.debug("Could not embed media %s", path, exc_info=True)
            break

    story.append(Paragraph("5. Recommendations & Action Timelines", h2))
    recs = report.get("recommendations") or []
    if not recs:
        story.append(Paragraph("No prioritized actions — no detections recorded.", body))
    else:
        for rec in recs:
            story.append(
                Paragraph(
                    f"<b>{rec.get('label')} ({rec.get('severity')})</b> — "
                    f"{rec.get('guidance')} Timeline: {rec.get('timeline')}.",
                    body,
                )
            )

    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            "Cost figures are offline heuristics (INR). RDS/Aurora was not contacted.",
            body,
        )
    )

    doc.build(story)
    return buf.getvalue()


_EXPORTERS = {
    "json": ("application/json", ".json", export_json_bytes),
    "csv": ("text/csv", ".csv", export_csv_bytes),
    "excel": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        export_excel_bytes,
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
        export_excel_bytes,
    ),
    "pdf": ("application/pdf", ".pdf", export_pdf_bytes),
}


def render_report_bytes(report: dict[str, Any], fmt: str) -> tuple[bytes, str, str]:
    """Return (content, mime, extension) for format key."""
    key = (fmt or "json").lower().strip()
    if key not in _EXPORTERS:
        raise ValueError(f"Unsupported report format: {fmt}")
    mime, ext, fn = _EXPORTERS[key]
    return fn(report), mime, ext


def write_report_file(
    inspection_id: int,
    fmt: str,
    *,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile (if needed), render, and write under generated_reports/."""
    if report is None:
        report = compile_inspection_report(inspection_id)
    content, mime, ext = render_report_bytes(report, fmt)
    code = (report.get("header") or {}).get("inspection_code") or f"INS-{inspection_id}"
    safe_code = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(code))
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"{safe_code}_{stamp}{ext}"
    out_path = reports_output_root() / filename
    out_path.write_bytes(content)
    return {
        "inspection_id": inspection_id,
        "format": fmt.lower(),
        "filename": filename,
        "path": str(out_path),
        "mime": mime,
        "size_bytes": len(content),
        "generated_at": report.get("generated_at"),
        "report_title": report.get("report_title"),
    }


def create_background_export_job(
    inspection_id: int,
    fmt: str = "pdf",
) -> dict[str, Any]:
    """Enqueue report export on the shared ThreadPoolExecutor."""
    from app.workers import executor as analysis_executor

    # Fail fast if inspection missing
    if load_inspection_record(inspection_id) is None:
        raise LookupError(f"Inspection {inspection_id} not found")

    job_id = str(uuid.uuid4())
    record = {
        "job_id": job_id,
        "inspection_id": inspection_id,
        "format": fmt.lower(),
        "status": "PENDING",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "result": None,
        "error": None,
    }
    with _lock:
        _report_jobs[job_id] = record

    app = current_app._get_current_object()

    def _run(app_obj, jid: str, insp_id: int, format_key: str) -> None:
        with app_obj.app_context():
            with _lock:
                job = _report_jobs.get(jid)
                if job:
                    job["status"] = "RUNNING"
            try:
                from app.workers.report_worker import generate_inspection_report

                result = generate_inspection_report(insp_id, format_key)
                with _lock:
                    job = _report_jobs.get(jid)
                    if job:
                        job["status"] = "SUCCEEDED"
                        job["result"] = result
                        job["completed_at"] = datetime.utcnow().isoformat() + "Z"
            except Exception as exc:
                logger.exception("Report job %s failed", jid)
                with _lock:
                    job = _report_jobs.get(jid)
                    if job:
                        job["status"] = "FAILED"
                        job["error"] = str(exc)
                        job["completed_at"] = datetime.utcnow().isoformat() + "Z"

    analysis_executor.submit(_run, app, job_id, inspection_id, fmt)
    return deepcopy(record)
