"""Contractor-only workbench: assigned repairs, after-image upload, completion."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.services.offline_store import offline_store
from app.services.repair_service import get_repair, list_repairs, repair_stats, update_repair
from app.utils.media_urls import get_before_media, media_public_url
from app.services.workflow_service import save_after_evidence

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
        rows = [r for r in rows if r.get("status") in {"open", "assigned", "in_progress", "rejected"}]
    elif status == "in_progress":
        rows = [r for r in rows if r.get("status") == "in_progress"]
    elif status == "completed":
        rows = [r for r in rows if r.get("status") in {"evidence_uploaded", "closed"}]
    elif status == "verified":
        rows = [r for r in rows if r.get("status") == "verified"]
    elif status == "paid":
        rows = [
            r
            for r in rows
            if (r.get("payment_status") == "paid" or r.get("status") == "paid")
        ]
    elif status == "payments":
        # Payment workbench: verified awaiting pay, paid, and closed.
        rows = [
            r
            for r in rows
            if r.get("status") in {"verified", "paid", "closed"}
            or (r.get("payment_status") or "").lower() == "paid"
        ]
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
        fields = {}
        saved = save_after_evidence(repair_id, uploaded)
        if saved:
            fields.update(saved)
        elif action == "complete" and not job.get("evidence_path") and not job.get("evidence_filename"):
            flash("Upload an after-repair photo before marking complete.", "danger")
            decorated = _decorate(job)
            return render_template(
                "contractor/detail.html",
                repair=decorated,
                before_url=_before_url(job),
                before_missing=not _before_url(job),
            )
        if notes:
            fields["completion_notes"] = notes
            fields["evidence_note"] = notes
        if action == "complete" and (saved or job.get("evidence_path") or job.get("evidence_filename")):
            fields["status"] = "evidence_uploaded"
            fields["completion_notes"] = notes
        if fields:
            update_repair(repair_id, **fields)
            flash(
                "After image saved. Municipal staff can verify and update payment next.",
                "success",
            )
            return redirect(url_for("contractor.job_detail", repair_id=repair_id))
        flash("No changes to save.", "warning")
        return redirect(url_for("contractor.job_detail", repair_id=repair_id))

    decorated = _decorate(get_repair(repair_id) or job)
    after_url = None
    if decorated.get("evidence_path"):
        after_url = url_for("contractor.after_image", repair_id=repair_id)
    return render_template(
        "contractor/detail.html",
        repair=decorated,
        before_url=_before_url(job),
        before_missing=not _before_url(job),
        after_url=after_url,
    )


@contractor_bp.route("/jobs/<int:repair_id>/after-image")
@login_required
@roles_required("contractor")
def after_image(repair_id: int):
    from pathlib import Path

    from flask import abort, send_file

    job = get_repair(repair_id)
    if not _owns(job) or not job.get("evidence_path"):
        abort(404)
    path = Path(job["evidence_path"])
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", as_attachment=False, download_name=path.name)
