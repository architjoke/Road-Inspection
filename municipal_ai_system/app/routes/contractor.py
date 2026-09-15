"""Contractor-only workbench: assigned repairs, after-image upload, completion."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.services.offline_store import offline_store
from app.services.repair_service import get_repair, list_repairs, repair_stats, update_repair
from app.utils.media_urls import get_before_media, media_public_url

contractor_bp = Blueprint("contractor", __name__, url_prefix="/contractor")

STATUS_LABELS = {
    "open": "Awaiting Upload",
    "assigned": "Awaiting Upload",
    "in_progress": "In Progress",
    "evidence_uploaded": "Submitted for Verification",
    "verified": "Verified by Manager",
    "paid": "Payment Processed",
    "closed": "Completed",
    "rejected": "Needs Rework",
}


def _contractor_id():
    return getattr(current_user, "id", None) or getattr(current_user, "pk", None)


def _owns(job: dict) -> bool:
    cid = _contractor_id()
    return job and (
        job.get("assignee_id") == cid or job.get("contractor_user_id") == cid
    )


def _decorate(job: dict) -> dict:
    row = dict(job)
    row["status_label"] = STATUS_LABELS.get(row.get("status"), row.get("status"))
    pay = (row.get("payment_status") or "pending").lower()
    if row.get("status") == "verified" and pay == "pending":
        row["payment_label"] = "Pending"
    elif pay == "paid" or row.get("status") == "paid":
        row["payment_label"] = "Paid"
    elif row.get("status") in {"verified", "closed"}:
        row["payment_label"] = "Verified"
    else:
        row["payment_label"] = "Pending"
    return row


def _before_url(job: dict) -> str | None:
    inspection_id = job.get("inspection_id")
    if not inspection_id:
        return None
    inspection = offline_store.get_inspection(inspection_id)
    if inspection is None:
        try:
            from app.models.inspection import Inspection
            from app.extensions import db

            inspection = db.session.get(Inspection, inspection_id)
        except Exception:
            inspection = None
    media = get_before_media(inspection) if inspection else None
    return media_public_url(media)


@contractor_bp.route("/")
@login_required
@roles_required("contractor")
def home():
    return redirect(url_for("contractor.jobs"))


@contractor_bp.route("/jobs")
@login_required
@roles_required("contractor")
def jobs():
    status = request.args.get("status") or ""
    rows = [_decorate(j) for j in list_repairs(assignee_id=_contractor_id())]
    if status == "pending_upload":
        rows = [r for r in rows if r.get("status") in {"open", "assigned"}]
    elif status == "in_progress":
        rows = [r for r in rows if r.get("status") == "in_progress"]
    elif status == "completed":
        rows = [r for r in rows if r.get("status") in {"evidence_uploaded", "closed"}]
    elif status == "verified":
        rows = [r for r in rows if r.get("status") == "verified"]
    elif status == "paid":
        rows = [r for r in rows if (r.get("payment_status") == "paid" or r.get("status") == "paid")]
    stats = repair_stats(assignee_id=_contractor_id())
    return render_template(
        "contractor/jobs.html",
        jobs=rows,
        status_filter=status,
        stats=stats,
    )


@contractor_bp.route("/jobs/<int:repair_id>", methods=["GET", "POST"])
@login_required
@roles_required("contractor")
def job_detail(repair_id: int):
    job = get_repair(repair_id)
    if not _owns(job):
        flash("That repair is not assigned to you.", "danger")
        return redirect(url_for("contractor.jobs"))

    if request.method == "POST":
        action = request.form.get("action")
        uploaded = request.files.get("after_image")
        notes = request.form.get("completion_notes") or request.form.get("notes")
        filename = uploaded.filename if uploaded and uploaded.filename else None
        fields = {}
        if filename:
            fields["evidence_filename"] = filename
            fields["status"] = "evidence_uploaded"
        if notes:
            fields["completion_notes"] = notes
            fields["evidence_note"] = notes
        if action == "complete":
            fields["status"] = "evidence_uploaded"
            fields["completion_notes"] = notes
        if fields:
            update_repair(repair_id, **fields)
            flash("Repair update saved.", "success")
            return redirect(url_for("contractor.job_detail", repair_id=repair_id))

    decorated = _decorate(job)
    return render_template(
        "contractor/detail.html",
        repair=decorated,
        before_url=_before_url(job),
        before_missing=not _before_url(job),
    )
