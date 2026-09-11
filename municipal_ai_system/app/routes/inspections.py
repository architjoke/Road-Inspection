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
from app.services.inspection_service import create_inspection_with_media
from app.services.validation_service import validate_upload
from app.services.ward_service import list_wards_for_municipality


inspections_bp = Blueprint(
    "inspections",
    __name__,
    url_prefix="/inspections"
)


def get_inspection_or_404(inspection_id: int) -> Inspection:
    inspection = db.session.get(Inspection, inspection_id)

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
    inspections = (
        Inspection.query
        .filter(Inspection.user_id == current_user.id)
        .order_by(Inspection.id.desc())
        .all()
    )

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
    query = Inspection.query

    if not _is_super_admin(current_user):
        query = query.filter(
            Inspection.municipality_id == current_user.municipality_id
        )

    inspections = query.order_by(Inspection.id.desc()).all()
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

    return render_template(
        "inspections/detail.html",
        inspection=inspection
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
