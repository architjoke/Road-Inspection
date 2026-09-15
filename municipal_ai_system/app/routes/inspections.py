from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import db
from app.models.inspection import Inspection
from app.models.inspection_media import InspectionMedia
from app.services.offline_store import offline_mode_enabled, offline_store
from app.services.inspection_service import create_inspection_with_media
from app.services.validation_service import validate_upload
from app.services.ward_service import list_wards_for_municipality
from app.utils.media_urls import get_before_media, media_public_url


inspections_bp = Blueprint(
    "inspections",
    __name__,
    url_prefix="/inspections"
)


def get_inspection_or_404(inspection_id: int):
    inspection = None
    if not offline_mode_enabled():
        try:
            inspection = db.session.get(Inspection, inspection_id)
        except Exception:
            db.session.rollback()
            inspection = None
    if inspection is None:
        inspection = offline_store.get_inspection(inspection_id)
    if inspection is None:
        abort(404)
    return inspection


def get_media_or_404(media_id: int) -> InspectionMedia:
    media = db.session.get(InspectionMedia, media_id)

    if media is None:
        abort(404)

    return media


def _is_super_admin(user) -> bool:
    flag = getattr(user, "is_super_admin", False)
    return flag() if callable(flag) else bool(flag)


def can_view_inspection(inspection: Inspection) -> bool:
    if _is_super_admin(current_user):
        return True

    if current_user.is_field_worker():
        return inspection.user_id == current_user.id

    flag = getattr(current_user, "is_contractor", False)
    if flag() if callable(flag) else bool(flag):
        from app.services.repair_service import list_repairs

        cid = getattr(current_user, "id", None) or getattr(current_user, "pk", None)
        return any(
            row.get("inspection_id") == inspection.id
            for row in list_repairs(assignee_id=cid)
        )

    return inspection.municipality_id == current_user.municipality_id


@inspections_bp.route("/new", methods=["GET", "POST"])
@login_required
@roles_required("field_worker", "municipal_admin", "super_admin")
def new():
    if request.method == "POST":
        uploaded_file = request.files.get("media_file")

        validation = validate_upload(uploaded_file)
        if not validation.get("valid"):
            flash(validation.get("error", "Invalid upload."), "danger")
            return render_template("inspections/new.html")

        for warning in validation.get("warnings") or []:
            flash(warning, "warning")

        try:
            inspection = create_inspection_with_media(
                current_user=current_user,
                form_data=request.form,
                uploaded_file=uploaded_file
            )

            media = inspection.media_items[0] if inspection.media_items else None
            image_url = (media.cdn_url or media.file_path) if media else None
            flash(
                (
                    f"Inspection {inspection.inspection_code} submitted. "
                    f"GPS: {inspection.location_latitude}, "
                    f"{inspection.location_longitude}. "
                    f"{'ImageKit URL saved.' if media and media.cdn_url else 'Media stored locally.'}"
                ),
                "success"
            )
            if image_url and media and media.cdn_url:
                flash(f"Media URL: {image_url}", "info")

            return redirect(
                url_for(
                    "inspections.detail",
                    inspection_id=inspection.id
                )
            )

        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")
            return render_template("inspections/new.html")

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Could not create inspection."
            )

            flash(
                "Inspection could not be saved. Please check the information "
                "and try again.",
                "danger"
            )

            return render_template("inspections/new.html")

    return render_template("inspections/new.html")


@inspections_bp.route("/my")
@login_required
@roles_required("field_worker")
def my_inspections():
    if offline_mode_enabled():
        inspections = [
            item
            for item in offline_store.list_inspections()
            if item.user_id == current_user.id
        ]
        inspections.sort(key=lambda row: row.id or 0, reverse=True)
    else:
        try:
            inspections = (
                Inspection.query
                .filter(Inspection.user_id == current_user.id)
                .order_by(Inspection.id.desc())
                .all()
            )
        except Exception:
            db.session.rollback()
            inspections = []

    return render_template(
        "inspections/my_inspections.html",
        inspections=inspections
    )


@inspections_bp.route("/")
@login_required
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer"
)
def list_all():
    if offline_mode_enabled():
        inspections = list(offline_store.list_inspections())
        if not _is_super_admin(current_user):
            mid = getattr(current_user, "municipality_id", None)
            if mid is not None:
                inspections = [i for i in inspections if i.municipality_id == mid]
        inspections.sort(key=lambda row: row.id or 0, reverse=True)
        wards = []
    else:
        query = Inspection.query

        if not _is_super_admin(current_user):
            mid = current_user.municipality_id
            if mid is not None:
                query = query.filter(Inspection.municipality_id == mid)

        try:
            inspections = query.order_by(Inspection.id.desc()).all()
        except Exception:
            db.session.rollback()
            inspections = []
        wards = list_wards_for_municipality(
            None if _is_super_admin(current_user) else current_user.municipality_id
        )

    return render_template(
        "inspections/list.html",
        inspections=inspections,
        wards=wards,
    )


@inspections_bp.route("/<int:inspection_id>")
@login_required
def detail(inspection_id: int):
    inspection = get_inspection_or_404(inspection_id)

    if not can_view_inspection(inspection):
        abort(403)

    media = get_before_media(inspection)
    before_url = media_public_url(media)
    return render_template(
        "inspections/detail.html",
        inspection=inspection,
        before_url=before_url,
        before_missing=not before_url,
    )


@inspections_bp.route("/media/<int:media_id>/view")
@login_required
def view_media(media_id: int):
    media = get_media_or_404(media_id)
    inspection = get_inspection_or_404(media.inspection_id)

    if not can_view_inspection(inspection):
        abort(403)

    # Prefer ImageKit CDN when available
    if media.cdn_url and str(media.cdn_url).startswith("http"):
        return redirect(media.cdn_url)

    if media.file_path and str(media.file_path).startswith("http"):
        return redirect(media.file_path)

    file_path = Path(media.file_path)

    if not file_path.exists() or not file_path.is_file():
        abort(404)

    return send_file(
        file_path,
        mimetype=media.mime_type,
        as_attachment=False,
        download_name=media.file_name
    )
