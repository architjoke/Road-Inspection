from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.auth.passwords import check_password, hash_password
from app.decorators import roles_required
from app.extensions import db
from app.models import get_model
from app.services.directory_service import (
    create_municipality,
    create_user,
    create_ward,
    default_role_choices,
    get_municipality,
    get_user,
    list_directory_wards,
    list_municipalities,
    list_users,
    update_user,
)
from app.services.offline_store import offline_mode_enabled, offline_store
from app.services.repair_service import (
    get_repair,
    list_repairs,
    repair_stats,
    update_repair,
)
from app.services.ward_service import list_wards_for_municipality
from app.services.workflow_service import (
    create_linked_repair,
    list_contractor_users,
    resolve_assignee_name,
    save_after_evidence,
)

protected_bp = Blueprint("protected", __name__)

ADMIN_ROLES = ("super_admin", "municipal_admin")
STAFF_ROLES = (
    "super_admin",
    "municipal_admin",
    "field_officer",
)


def _user_flag(name: str) -> bool:
    flag = getattr(current_user, name, False)
    return flag() if callable(flag) else bool(flag)


def _is_super() -> bool:
    return _user_flag("is_super_admin")


def _is_contractor() -> bool:
    return _user_flag("is_contractor")


def _municipality_scope():
    if _is_super():
        return None
    return getattr(current_user, "municipality_id", None)


def _safe_int(value, default=None):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@protected_bp.route("/users")
@roles_required(*ADMIN_ROLES)
def users():
    rows = list_users(municipality_id=_municipality_scope())
    return render_template(
        "users/list.html",
        page_title="User Management",
        users=rows,
        database_configured=bool(current_app.config.get("DATABASE_CONFIGURED")),
    )


@protected_bp.route("/users/new", methods=["GET", "POST"])
@roles_required(*ADMIN_ROLES)
def user_create():
    if request.method == "POST":
        municipality_id = _safe_int(request.form.get("municipality_id"))
        if not _is_super():
            municipality_id = getattr(current_user, "municipality_id", None)
        row, error = create_user(
            username=request.form.get("username", ""),
            email=request.form.get("email", ""),
            full_name=request.form.get("full_name", ""),
            password=request.form.get("password", ""),
            role_id=request.form.get("role_id"),
            municipality_id=municipality_id,
            phone=request.form.get("phone") or None,
            is_active=request.form.get("is_active") == "1",
        )
        if error:
            flash(error, "danger")
        else:
            flash(f"User {row.username} created.", "success")
            return redirect(url_for("protected.users"))
    return render_template(
        "users/create.html",
        page_title="Create User",
        roles=default_role_choices(),
        municipalities=list_municipalities(),
        is_super=_is_super(),
        default_municipality_id=getattr(current_user, "municipality_id", None),
    )


@protected_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required(*ADMIN_ROLES)
def user_edit(user_id: int):
    row = get_user(user_id)
    if row is None:
        flash("User not found.", "danger")
        return redirect(url_for("protected.users"))
    if request.method == "POST":
        municipality_id = _safe_int(request.form.get("municipality_id"))
        if not _is_super():
            municipality_id = getattr(current_user, "municipality_id", None)
        updated, error = update_user(
            user_id,
            full_name=request.form.get("full_name"),
            email=request.form.get("email"),
            phone=request.form.get("phone"),
            role_id=_safe_int(request.form.get("role_id")),
            municipality_id=municipality_id,
            is_active=request.form.get("is_active") == "1",
            password=request.form.get("password") or None,
        )
        if error:
            flash(error, "danger")
        else:
            flash(f"User {updated.username} updated.", "success")
            return redirect(url_for("protected.users"))
        row = updated or row
    return render_template(
        "users/edit.html",
        page_title="Edit User",
        account=row,
        roles=default_role_choices(),
        municipalities=list_municipalities(),
        is_super=_is_super(),
    )


@protected_bp.route("/municipalities")
@roles_required("super_admin")
def municipalities():
    rows = list_municipalities()
    return render_template(
        "municipalities/list.html",
        page_title="Municipalities",
        municipalities=rows,
        database_configured=bool(current_app.config.get("DATABASE_CONFIGURED")),
    )


@protected_bp.route("/municipalities/new", methods=["GET", "POST"])
@roles_required("super_admin")
def municipality_create():
    if request.method == "POST":
        row, error = create_municipality(
            name=request.form.get("name"),
            code=request.form.get("code"),
            region=request.form.get("region"),
            state=request.form.get("state"),
            country=request.form.get("country"),
            contact_email=request.form.get("contact_email"),
            contact_phone=request.form.get("contact_phone"),
            address=request.form.get("address"),
        )
        if error:
            flash(error, "danger")
        else:
            flash(f"Municipality {row.name} created.", "success")
            return redirect(url_for("protected.municipalities"))
    return render_template(
        "municipalities/form.html",
        page_title="Add Municipality",
        municipality=None,
    )


@protected_bp.route("/municipalities/<int:municipality_id>")
@roles_required("super_admin")
def municipality_detail(municipality_id: int):
    row = get_municipality(municipality_id)
    if row is None:
        flash("Municipality not found.", "danger")
        return redirect(url_for("protected.municipalities"))
    wards = list_directory_wards(municipality_id)
    return render_template(
        "municipalities/detail.html",
        page_title=getattr(row, "name", "Municipality"),
        municipality=row,
        wards=wards,
    )


@protected_bp.route("/wards")
@roles_required(*ADMIN_ROLES)
def wards():
    rows = list_directory_wards(_municipality_scope())
    return render_template(
        "wards/list.html",
        page_title="Wards",
        wards=rows,
        database_configured=bool(current_app.config.get("DATABASE_CONFIGURED")),
    )


@protected_bp.route("/wards/new", methods=["GET", "POST"])
@roles_required(*ADMIN_ROLES)
def ward_create():
    municipalities = list_municipalities()
    if request.method == "POST":
        municipality_id = _safe_int(request.form.get("municipality_id"))
        if not _is_super():
            municipality_id = getattr(current_user, "municipality_id", None)
        row, error = create_ward(
            name=request.form.get("name"),
            code=request.form.get("code"),
            municipality_id=municipality_id,
            administrator_name=request.form.get("administrator_name"),
            administrator_email=request.form.get("administrator_email"),
            administrator_phone=request.form.get("administrator_phone"),
            population=request.form.get("population"),
        )
        if error:
            flash(error, "danger")
        else:
            boundary = (request.form.get("boundary_geojson") or "").strip()
            if boundary:
                _save_ward_boundary(row.id, boundary)
            flash(f"Ward {row.name} created.", "success")
            return redirect(url_for("protected.wards"))
    return render_template(
        "wards/form.html",
        page_title="Add Ward",
        municipalities=municipalities,
        is_super=_is_super(),
        default_municipality_id=getattr(current_user, "municipality_id", None),
        ward=None,
        boundary_geojson="",
    )


def _save_ward_boundary(ward_id: int, boundary: str) -> bool:
    from app.services.ward_service import set_ward_boundary

    try:
        result = set_ward_boundary(ward_id, boundary)
    except ValueError as exc:
        flash(f"Boundary not saved: {exc}", "warning")
        return False
    except Exception:
        current_app.logger.exception("Saving boundary for ward %s failed", ward_id)
        flash("Boundary not saved: database error.", "danger")
        return False
    flash(
        f"Boundary saved ({result['area_sq_km'] or 0:.2f} km²). "
        f"Assigned this ward to {result['inspections_assigned']} existing inspection(s) "
        f"and {result['detections_assigned']} detection(s).",
        "success",
    )
    return True


@protected_bp.route("/wards/<int:ward_id>/edit", methods=["GET", "POST"])
@roles_required(*ADMIN_ROLES)
def ward_edit(ward_id: int):
    from app.models.ward import Ward
    from app.services.ward_service import get_ward_boundary_geojson, update_ward_fields

    if offline_mode_enabled():
        flash("Editing wards needs the database (offline mode is on).", "warning")
        return redirect(url_for("protected.wards"))
    ward = db.session.get(Ward, ward_id)
    scope = _municipality_scope()
    if ward is None or (scope is not None and ward.municipality_id != scope):
        abort(404)
    if request.method == "POST":
        try:
            update_ward_fields(ward_id, **request.form.to_dict())
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            boundary = (request.form.get("boundary_geojson") or "").strip()
            if boundary:
                _save_ward_boundary(ward_id, boundary)
            else:
                flash("Ward details saved.", "success")
            return redirect(url_for("protected.wards"))
    return render_template(
        "wards/form.html",
        page_title=f"Edit {ward.name}",
        municipalities=list_municipalities(),
        is_super=False,
        default_municipality_id=ward.municipality_id,
        ward=ward,
        boundary_geojson=get_ward_boundary_geojson(ward_id) or "",
    )


@protected_bp.route("/detections")
@roles_required("super_admin", "municipal_admin", "field_officer")
def detections():
    from app.services.detection_review_service import list_detections_for_review

    from app.services.access_service import filter_rows_by_inspection

    payload = list_detections_for_review()
    payload["detections"] = filter_rows_by_inspection(payload.get("detections") or [])
    return render_template(
        "detections/review.html",
        page_title="AI Detection Review",
        detections=payload.get("detections") or [],
        persistence_mode=payload.get("persistence_mode"),
        note=payload.get("note"),
    )


@protected_bp.route("/detections/<int:detection_id>", methods=["GET", "POST"])
@roles_required("super_admin", "municipal_admin", "field_officer")
def detection_detail(detection_id: int):
    from app.services.access_service import filter_rows_by_inspection
    from app.services.detection_review_service import get_detection_for_review
    from app.services.review_service import review_detection

    visible = get_detection_for_review(detection_id)
    if visible is not None and not filter_rows_by_inspection([visible]):
        abort(404)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_repair":
            row = get_detection_for_review(detection_id) or {}
            assignee_id = _safe_int(request.form.get("assignee_id"))
            existing_id = row.get("repair_id")
            if existing_id:
                repair = update_repair(
                    int(existing_id),
                    assignee_id=assignee_id,
                    assignee_name=resolve_assignee_name(assignee_id) if assignee_id else None,
                    notes=request.form.get("notes") or None,
                )
                flash(
                    f"Repair job #{existing_id} updated"
                    + (" and assigned." if assignee_id else "."),
                    "success",
                )
                return redirect(url_for("protected.repair_detail", repair_id=int(existing_id)))
            repair = create_linked_repair(
                inspection_id=row.get("inspection_id"),
                detection_id=detection_id,
                damage_type=row.get("damage_type") or row.get("class_name"),
                severity=row.get("severity"),
                assignee_id=assignee_id,
                notes=request.form.get("notes"),
            )
            flash(f"Repair job #{repair['id']} created and linked to this detection.", "success")
            return redirect(url_for("protected.repair_detail", repair_id=repair["id"]))
        try:
            updated = review_detection(
                detection_id,
                decision=request.form.get("decision", ""),
                reviewer_name=getattr(current_user, "full_name", None),
                reviewer_id=getattr(current_user, "pk", None),
                comment=request.form.get("comment"),
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            updated = None
        if updated is None:
            flash("Detection not found.", "danger")
        else:
            flash("Review saved.", "success")
        return redirect(url_for("protected.detection_detail", detection_id=detection_id))

    payload = get_detection_for_review(detection_id)
    if payload is None:
        flash("Detection not found.", "danger")
        return redirect(url_for("protected.detections"))
    contractors = list_contractor_users(_municipality_scope())
    before_url = None
    annotated_url = None
    try:
        from app.services.workflow_service import get_inspection_by_id
        from app.utils.media_urls import (
            annotated_media_url,
            get_before_media,
            media_public_url,
        )

        inspection = get_inspection_by_id(payload.get("inspection_id"))
        media = get_before_media(inspection) if inspection else None
        before_url = media_public_url(media)
        annotated_url = annotated_media_url(media)
    except Exception:
        current_app.logger.exception("Could not resolve detection media")
    return render_template(
        "detections/detail.html",
        page_title=f"Detection {detection_id}",
        detection=payload,
        contractors=contractors,
        before_url=before_url,
        annotated_url=annotated_url,
    )


@protected_bp.route("/repairs")
@roles_required(*STAFF_ROLES)
def repairs():
    if _is_contractor() and not _is_super():
        return redirect(url_for("contractor.jobs"))
    history = request.args.get("view") == "history"
    rows = list_repairs(assignee_id=None, history=history)
    return render_template(
        "repairs/list.html",
        page_title="Repair History" if history else "Repair Tasks",
        repairs=rows,
        history=history,
    )


@protected_bp.route("/repairs/verify")
@roles_required("super_admin", "municipal_admin", "field_officer")
def repairs_verify():
    inspection_id = request.args.get("inspection_id", type=int)
    repair_id = request.args.get("repair_id", type=int)
    before_url = None
    after_url = None
    before_error = None
    before_has_boxes = False
    repair = get_repair(repair_id) if repair_id else None
    if repair and not inspection_id:
        inspection_id = repair.get("inspection_id")
    pending_verify = []
    if not repair_id and not inspection_id:
        pending_verify = list_repairs(status="evidence_uploaded")
        # Also surface partial / rejected-for-rework if present
        for status in ("partial", "rejected"):
            pending_verify.extend(list_repairs(status=status))
    if inspection_id:
        from app.services.offline_store import offline_mode_enabled, offline_store
        from app.utils.media_urls import (
            annotated_media_url,
            get_before_media,
            media_public_url,
        )

        inspection = offline_store.get_inspection(inspection_id) if offline_mode_enabled() else None
        if inspection is None:
            Inspection = get_model("inspections")
            if Inspection is not None:
                try:
                    inspection = db.session.get(Inspection, inspection_id)
                except Exception:
                    inspection = None
        media = get_before_media(inspection) if inspection else None
        annotated = annotated_media_url(media)
        original = media_public_url(media)
        # Prefer annotated (bounding boxes) for the verification UI.
        before_url = annotated or original
        before_has_boxes = bool(annotated)
        if not before_url:
            before_error = "No before image available for this inspection ID"
    if repair and repair.get("evidence_path"):
        after_url = url_for("protected.repair_after_image", repair_id=repair["id"])
    return render_template(
        "repairs/manager.html",
        inspection_id=inspection_id,
        repair_id=repair_id,
        repair=repair,
        before_url=before_url,
        after_url=after_url,
        before_error=before_error,
        before_has_boxes=before_has_boxes,
        pending_verify=pending_verify,
    )


@protected_bp.route("/repairs/<int:repair_id>/after-image")
@roles_required(*STAFF_ROLES)
def repair_after_image(repair_id: int):
    from pathlib import Path

    from flask import abort, send_file

    row = get_repair(repair_id)
    if row is None or not row.get("evidence_path"):
        abort(404)
    path = Path(row["evidence_path"])
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", as_attachment=False, download_name=path.name)


@protected_bp.route("/repairs/new", methods=["GET", "POST"])
@roles_required("super_admin", "municipal_admin", "field_officer")
def repair_create():
    if request.method == "POST":
        assignee_id = _safe_int(request.form.get("assignee_id"))
        row = create_linked_repair(
            inspection_id=_safe_int(request.form.get("inspection_id")),
            detection_id=_safe_int(request.form.get("detection_id")),
            location=request.form.get("location"),
            damage_type=request.form.get("damage_type"),
            severity=request.form.get("severity") or "MODERATE",
            assignee_id=assignee_id,
            notes=request.form.get("notes"),
        )
        flash(f"Repair job #{row['id']} created.", "success")
        return redirect(url_for("protected.repair_detail", repair_id=row["id"]))
    contractors = list_contractor_users(_municipality_scope())
    prefill_inspection = request.args.get("inspection_id", type=int)
    prefill_detection = request.args.get("detection_id", type=int)
    return render_template(
        "repairs/form.html",
        page_title="Assign Repair",
        contractors=contractors,
        prefill_inspection=prefill_inspection,
        prefill_detection=prefill_detection,
    )


@protected_bp.route("/repairs/<int:repair_id>", methods=["GET", "POST"])
@roles_required(*STAFF_ROLES)
def repair_detail(repair_id: int):
    row = get_repair(repair_id)
    if row is None:
        flash("Repair job not found.", "danger")
        return redirect(url_for("protected.repairs"))
    if request.method == "POST":
        status = request.form.get("status")
        assignee_id = _safe_int(request.form.get("assignee_id"))
        notes = request.form.get("notes")
        update_repair(
            repair_id,
            status=status,
            assignee_id=assignee_id,
            assignee_name=resolve_assignee_name(assignee_id) if assignee_id else None,
            notes=notes,
        )
        pay = request.form.get("payment_status")
        if pay == "paid":
            # Paid is terminal for payment; keep job verified/closed semantics.
            update_repair(repair_id, payment_status="paid", status="paid")
        elif pay in {"pending", "verified"}:
            update_repair(repair_id, payment_status=pay)
        flash("Repair job updated.", "success")
        return redirect(url_for("protected.repair_detail", repair_id=repair_id))
    contractors = list_contractor_users(_municipality_scope())
    return render_template(
        "repairs/detail.html",
        page_title=f"Repair #{repair_id}",
        repair=row,
        contractors=contractors,
    )


@protected_bp.route("/repairs/<int:repair_id>/evidence", methods=["GET", "POST"])
@roles_required(*STAFF_ROLES)
def repair_evidence(repair_id: int):
    row = get_repair(repair_id)
    if row is None:
        flash("Repair job not found.", "danger")
        return redirect(url_for("protected.repairs"))
    if request.method == "POST":
        uploaded = request.files.get("evidence")
        fields = save_after_evidence(repair_id, uploaded)
        if not fields:
            flash("Choose an after-repair image to upload.", "danger")
            return render_template(
                "repairs/upload_evidence.html",
                page_title="Upload repair evidence",
                repair=row,
            )
        fields["evidence_note"] = request.form.get("evidence_note")
        update_repair(repair_id, **fields)
        flash("Evidence saved. Run SSIM verification next.", "success")
        return redirect(
            url_for(
                "protected.repairs_verify",
                repair_id=repair_id,
                inspection_id=row.get("inspection_id"),
            )
        )
    return render_template(
        "repairs/upload_evidence.html",
        page_title="Upload repair evidence",
        repair=row,
    )

@protected_bp.route("/repairs/locations")
@roles_required(*STAFF_ROLES)
def repair_locations():
    if _is_contractor() and not _is_super():
        return redirect(url_for("contractor.jobs"))
    rows = list_repairs(assignee_id=None)
    return render_template(
        "repairs/locations.html",
        page_title="Repair Locations",
        repairs=rows,
    )


@protected_bp.route("/map")
@roles_required(
    "super_admin",
    "municipal_admin",
    "field_officer",
    "viewer",
    "field_worker",
    "contractor",
)
def map_view():
    municipality_id = _municipality_scope()
    wards = []
    if current_app.config.get("DATABASE_CONFIGURED") and not offline_mode_enabled():
        try:
            wards = list_wards_for_municipality(municipality_id)
        except Exception:
            db.session.rollback()
            wards = []
    return render_template(
        "map/index.html",
        wards=wards,
        geojson_url="/api/map/detections",
        wards_url="/api/wards/boundaries",
        radius_url="/api/inspections/radius",
    )


@protected_bp.route("/reports")
@roles_required("super_admin", "municipal_admin", "field_officer", "viewer")
def reports():
    inspections = []
    if offline_mode_enabled():
        inspections = sorted(
            offline_store.list_inspections(),
            key=lambda r: r.id,
            reverse=True,
        )[:25]
    elif current_app.config.get("DATABASE_CONFIGURED"):
        Inspection = get_model("inspections")
        if Inspection is not None:
            try:
                inspections = (
                    db.session.query(Inspection)
                    .order_by(Inspection.id.desc())
                    .limit(25)
                    .all()
                )
            except Exception:
                db.session.rollback()
                inspections = []
    return render_template(
        "reports/list.html",
        inspections=inspections,
        offline_mode=offline_mode_enabled(),
    )


@protected_bp.route("/settings")
@roles_required("super_admin")
def settings():
    from app.services.imagekit_service import is_imagekit_configured

    imagekit_ok = False
    try:
        imagekit_ok = is_imagekit_configured()
    except Exception:
        imagekit_ok = False

    return render_template(
        "settings/index.html",
        page_title="System Settings",
        offline_mode=bool(current_app.config.get("OFFLINE_MODE")),
        database_configured=bool(current_app.config.get("DATABASE_CONFIGURED")),
        imagekit_configured=imagekit_ok,
        yolo_device=current_app.config.get("YOLO_DEVICE", "cpu"),
        yolo_force_mock=bool(current_app.config.get("YOLO_FORCE_MOCK")),
        flask_host=current_app.config.get("FLASK_HOST") or "0.0.0.0",
        flask_port=current_app.config.get("FLASK_PORT") or "5000",
    )


@protected_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if new_pw and new_pw != confirm:
            flash("New passwords do not match.", "danger")
        elif new_pw and len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_pw:
            pk = getattr(current_user, "id", None) or getattr(current_user, "pk", None)
            User = get_model("users")
            changed = False
            if (
                User is not None
                and current_app.config.get("DATABASE_CONFIGURED")
                and not offline_mode_enabled()
            ):
                try:
                    row = db.session.get(User, pk)
                    stored = getattr(row, "password_hash", None) if row else None
                    if row is None or not stored or not check_password(current_pw, stored):
                        flash("Current password is incorrect.", "danger")
                    else:
                        row.password_hash = hash_password(new_pw)
                        db.session.commit()
                        changed = True
                except Exception:
                    db.session.rollback()
                    flash("Could not update password on the database.", "danger")
            else:
                memory_user = get_user(pk) if pk else None
                stored = getattr(memory_user, "password_hash", None) if memory_user else None
                if stored and not check_password(current_pw, stored):
                    flash("Current password is incorrect.", "danger")
                else:
                    if memory_user:
                        update_user(pk, password=new_pw)
                    flash(
                        "Password updated for this session store. "
                        "On RDS, the live account password is unchanged until the database is connected.",
                        "warning",
                    )
                    changed = True
            if changed and current_app.config.get("DATABASE_CONFIGURED"):
                flash("Password updated.", "success")
        else:
            flash("No password change requested.", "info")
        return redirect(url_for("protected.profile"))
    contractor_jobs = []
    contractor_stats = {}
    if _is_contractor():
        cid = getattr(current_user, "id", None) or getattr(current_user, "pk", None)
        contractor_jobs = list_repairs(assignee_id=cid)
        contractor_stats = repair_stats(assignee_id=cid)
    return render_template(
        "users/profile.html",
        page_title="Profile",
        user=current_user,
        contractor_jobs=contractor_jobs,
        contractor_stats=contractor_stats,
    )
