"""Super Admin model training / inference preview with bounding boxes."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from app.decorators import roles_required
from app.ai.model_loader import ModelUnavailableError, active_model_summary

training_bp = Blueprint("training", __name__, url_prefix="/training")


def _output_root() -> Path:
    root = Path(current_app.config.get("TEMP_UPLOAD_ROOT", "tmp")) / "training"
    root.mkdir(parents=True, exist_ok=True)
    return root


@training_bp.route("/", methods=["GET", "POST"])
@login_required
@roles_required("super_admin")
def index():
    annotated_url = None
    detections = []
    error = None
    if request.method == "POST":
        uploaded = request.files.get("image")
        if not uploaded or not uploaded.filename:
            flash("Choose an image to run detection.", "danger")
        else:
            try:
                from app.ai.image_annotator import annotate_detections
                from app.ai.yolo_inference import detect_image

                suffix = Path(secure_filename(uploaded.filename)).suffix or ".jpg"
                token = uuid4().hex
                source = _output_root() / f"{token}_source{suffix}"
                dest = _output_root() / f"{token}_annotated.jpg"
                uploaded.save(source)
                detections = detect_image(source)
                annotate_detections(source, detections, dest)
                annotated_url = url_for("training.annotated", token=token)
                if not detections:
                    flash("No damages detected above the confidence threshold.", "warning")
                else:
                    flash(f"Detected {len(detections)} damage region(s).", "success")
            except ModelUnavailableError as exc:
                current_app.logger.error("Training preview: %s", exc)
                error = str(exc)
                flash(f"Detection model unavailable: {exc}", "danger")
            except Exception as exc:
                current_app.logger.exception("Training preview failed")
                error = str(exc)
                flash("Could not run detection on that image. Try a JPG or PNG road photo.", "danger")

    return render_template(
        "training/index.html",
        annotated_url=annotated_url,
        detections=detections,
        error=error,
        model=active_model_summary(),
    )


@training_bp.route("/annotated/<token>")
@login_required
@roles_required("super_admin")
def annotated(token: str):
    path = _output_root() / f"{token}_annotated.jpg"
    if not path.is_file():
        flash("Annotated image expired or missing.", "danger")
        return render_template(
            "training/index.html", annotated_url=None, detections=[], error="missing",
            model=active_model_summary(),
        ), 404
    return send_file(path, mimetype="image/jpeg")
