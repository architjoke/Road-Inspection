"""Phase 6 inspection analysis worker."""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path

from app.ai.detection_postprocessor import (
    attach_depth_features,
    attach_segmentation_features,
    enrich_detections,
)
from app.ai.depth_estimation import (
    DepthError,
    estimate_depth,
    get_depth_config,
    serialize_depth_for_json,
)
from app.ai.image_annotator import annotate_depth_map, annotate_detections, annotate_segmentation
from app.ai.segmentation import (
    SegmentationError,
    get_segmentation_config,
    segment,
    serialize_segmentation_for_json,
)
from app.ai.yolo_inference import DetectionError, detect_image
from app.services.detection_service import persist_detections
from app.utils.status_constants import (
    MEDIA_FAILED,
    MEDIA_PROCESSED,
    MEDIA_PROCESSING,
    PENDING_REVIEW,
    PROCESSING,
)
from app.workers.task_store import (
    TASK_FAILED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    task_store,
)

logger = logging.getLogger(__name__)


def _resolve_local_media_path(media) -> Path | None:
    """Prefer local_path from Phase 5; else file_path if local."""
    candidates = []
    exif = getattr(media, "exif_data", None) or {}
    if isinstance(exif, dict) and exif.get("local_path"):
        candidates.append(exif["local_path"])

    file_path = getattr(media, "file_path", None)
    if file_path:
        candidates.append(file_path)

    thumbnail = getattr(media, "thumbnail_path", None)
    # Never use remote CDN as filesystem path
    for candidate in candidates:
        path = Path(str(candidate))
        if str(candidate).startswith("http"):
            continue
        if path.exists() and path.is_file():
            return path
    return None


def _update_inspection_status(inspection_id: int, status: str, total_detections: int | None = None):
    try:
        from app.services.offline_store import offline_mode_enabled, offline_store

        if offline_mode_enabled():
            fields = {"status": status, "updated_at": datetime.utcnow()}
            if total_detections is not None:
                fields["total_detections"] = total_detections
            offline_store.update_inspection(inspection_id, **fields)
            return

        from app.extensions import db
        from app.models.inspection import Inspection

        inspection = db.session.get(Inspection, inspection_id)
        if inspection is None:
            return
        inspection.status = status
        if total_detections is not None:
            inspection.total_detections = total_detections
        inspection.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        logger.exception("Could not update inspection %s status", inspection_id)
        try:
            from app.extensions import db

            db.session.rollback()
        except Exception:
            pass


def _update_media_status(media_id: int | None, status: str):
    if media_id is None:
        return
    try:
        from app.services.offline_store import offline_mode_enabled, offline_store

        if offline_mode_enabled():
            offline_store.update_media(
                media_id,
                processing_status=status,
                updated_at=datetime.utcnow(),
            )
            return

        from app.extensions import db
        from app.models.inspection_media import InspectionMedia

        media = db.session.get(InspectionMedia, media_id)
        if media is None:
            return
        media.processing_status = status
        media.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        logger.exception("Could not update media %s status", media_id)
        try:
            from app.extensions import db

            db.session.rollback()
        except Exception:
            pass


def _run_on_image(path: Path, frame_number: int | None = None) -> list[dict]:
    detections = detect_image(path, frame_number=frame_number)
    return enrich_detections(detections)


def _resolve_output_dir(cfg_dir: str, fallback: Path) -> Path:
    if cfg_dir:
        out = Path(cfg_dir)
        out.mkdir(parents=True, exist_ok=True)
        return out
    return fallback


def _run_optional_segmentation(
    path: Path, detections: list[dict]
) -> tuple[dict, list[dict], list[dict]]:
    """Returns (json_section, detections, raw_mask_results_with_arrays)."""
    cfg = get_segmentation_config()
    section = {
        "enabled": bool(cfg["enabled"]),
        "available": False,
        "results": [],
        "union_mask_pixel_count": 0,
        "union_damaged_area_percentage": 0.0,
        "model_version": None,
        "overlay_path": None,
        "error": None,
    }
    if not cfg["enabled"]:
        return section, detections, []

    try:
        payload = segment(path, detections=detections)
        raw_results = payload.get("results") or []
        updated = attach_segmentation_features(detections, raw_results)
        out_dir = _resolve_output_dir(cfg.get("output_dir") or "", path.parent)
        overlay = None
        if raw_results:
            overlay = annotate_segmentation(
                path,
                raw_results,
                out_dir / f"{path.stem}_seg_overlay.jpg",
                detections=updated,
            )
            for item in raw_results:
                item["mask_reference"] = overlay
        section = serialize_segmentation_for_json(payload)
        section["overlay_path"] = overlay
        return section, updated, raw_results
    except SegmentationError as exc:
        logger.warning("Segmentation unavailable/failed: %s", exc)
        section["error"] = str(exc)
        return section, detections, []
    except Exception as exc:
        logger.exception("Segmentation unexpected failure")
        section["error"] = str(exc)
        return section, detections, []


def _run_optional_depth(
    path: Path,
    detections: list[dict],
    raw_masks: list[dict],
) -> tuple[dict, list[dict]]:
    cfg = get_depth_config()
    section = {
        "enabled": bool(cfg["enabled"]),
        "available": False,
        "normalization": "relative",
        "representation": "relative_inverse_depth_style",
        "calibration_available": False,
        "metric_depth_available": False,
        "physical_volume_available": False,
        "results": [],
        "per_region": [],
        "overlay_path": None,
        "error": None,
    }
    if not cfg["enabled"]:
        return section, detections

    try:
        payload = estimate_depth(path, masks=raw_masks, detections=detections)
        updated = attach_depth_features(detections, payload)
        out_dir = _resolve_output_dir(cfg.get("output_dir") or "", path.parent)
        overlay = None
        if payload.get("depth_map") is not None:
            overlay = annotate_depth_map(
                path,
                payload["depth_map"],
                out_dir / f"{path.stem}_depth_overlay.jpg",
                regions=payload.get("per_region") or [],
            )
            payload["depth_map_reference"] = overlay
        section = serialize_depth_for_json(payload)
        section["overlay_path"] = overlay
        section["results"] = section.get("per_region") or []
        return section, updated
    except DepthError as exc:
        logger.warning("Depth unavailable/failed: %s", exc)
        section["error"] = str(exc)
        return section, detections
    except Exception as exc:
        logger.exception("Depth unexpected failure")
        section["error"] = str(exc)
        return section, detections


def _analyze_still_image(path: Path, frame_number: int | None = None) -> dict:
    detections = _run_on_image(path, frame_number=frame_number)
    errors: list[str] = []
    seg_section, detections, raw_masks = _run_optional_segmentation(path, detections)
    if seg_section.get("error"):
        errors.append(f"segmentation: {seg_section['error']}")
    depth_section, detections = _run_optional_depth(path, detections, raw_masks)
    if depth_section.get("error"):
        errors.append(f"depth: {depth_section['error']}")

    annotated_path = annotate_detections(
        path,
        detections,
        path.parent / f"{path.stem}_annotated.jpg",
    )
    return {
        "detections": detections,
        "segmentation": seg_section,
        "depth": depth_section,
        "annotated_path": annotated_path,
        "errors": errors,
    }


def analyze_inspection_job(
    task_id: str,
    inspection_id: int,
    media_id: int | None = None,
    app=None,
) -> None:
    """Worker entrypoint executed inside ThreadPoolExecutor."""
    started = time.monotonic()
    logger.info(
        "Analysis START task=%s inspection=%s media=%s",
        task_id,
        inspection_id,
        media_id,
    )
    task_store.update(
        task_id,
        status=TASK_RUNNING,
        started_at=datetime.utcnow(),
        progress=5.0,
    )

    def _work():
        from app.services.offline_store import offline_mode_enabled, offline_store

        if offline_mode_enabled():
            inspection = offline_store.get_inspection(inspection_id)
            if inspection is None:
                raise RuntimeError(f"Inspection {inspection_id} not found")
            media = None
            if media_id is not None:
                media = offline_store.get_media(media_id)
            if media is None and inspection.media_items:
                media = inspection.media_items[0]
        else:
            from app.extensions import db
            from app.models.inspection import Inspection
            from app.models.inspection_media import InspectionMedia

            inspection = db.session.get(Inspection, inspection_id)
            if inspection is None:
                raise RuntimeError(f"Inspection {inspection_id} not found")

            media = None
            if media_id is not None:
                media = db.session.get(InspectionMedia, media_id)
            if media is None and inspection.media_items:
                media = inspection.media_items[0]

        if media is None:
            raise RuntimeError(f"No media for inspection {inspection_id}")

        _update_inspection_status(inspection_id, PROCESSING)
        _update_media_status(media.id, MEDIA_PROCESSING)
        task_store.update(task_id, progress=15.0)

        local_path = _resolve_local_media_path(media)
        all_detections: list[dict] = []
        annotated_path = None
        segmentation_section = {
            "enabled": False,
            "available": False,
            "results": [],
            "error": None,
        }
        depth_section = {
            "enabled": False,
            "available": False,
            "normalization": "relative",
            "results": [],
            "error": None,
        }
        pipeline_errors: list[str] = []

        if media.media_type == "video":
            if local_path is None:
                raise RuntimeError("Local video file unavailable for analysis")
            from app.services.video_service import extract_keyframes

            with tempfile.TemporaryDirectory(prefix="frames_") as tmp:
                frame_paths = extract_keyframes(local_path, tmp, fps=1)
                task_store.update(task_id, progress=30.0)
                if not frame_paths:
                    raise RuntimeError("No keyframes extracted from video")
                # Cap frames for bounded offline runtime
                selected = frame_paths[:8]
                for idx, frame_path in enumerate(selected):
                    frame_result = _analyze_still_image(Path(frame_path), frame_number=idx)
                    all_detections.extend(frame_result["detections"])
                    pipeline_errors.extend(frame_result.get("errors") or [])
                    # Keep last frame's optional sections for summary
                    segmentation_section = frame_result["segmentation"]
                    depth_section = frame_result["depth"]
                    progress = 30.0 + (50.0 * (idx + 1) / max(len(selected), 1))
                    task_store.update(task_id, progress=progress)
                if selected:
                    annotated_path = annotate_detections(
                        selected[0],
                        all_detections[:20] or enrich_detections([]),
                        Path(local_path).parent
                        / f"{Path(local_path).stem}_annotated_frame0.jpg",
                    )
        else:
            if local_path is None:
                raise RuntimeError(
                    "Local image file unavailable (CDN-only media cannot be "
                    "analyzed offline without a local copy)"
                )
            still = _analyze_still_image(local_path)
            all_detections = still["detections"]
            segmentation_section = still["segmentation"]
            depth_section = still["depth"]
            annotated_path = still["annotated_path"]
            pipeline_errors = still.get("errors") or []
            task_store.update(task_id, progress=70.0)

        lat = float(inspection.location_latitude) if inspection.location_latitude is not None else None
        lng = float(inspection.location_longitude) if inspection.location_longitude is not None else None

        persist_result = persist_detections(
            inspection_id=inspection_id,
            media_id=media.id,
            detections=all_detections,
            latitude=lat,
            longitude=lng,
        )
        task_store.update(task_id, progress=90.0)

        _update_inspection_status(
            inspection_id,
            PENDING_REVIEW,
            total_detections=len(all_detections),
        )
        _update_media_status(media.id, MEDIA_PROCESSED)

        summary = {
            "inspection_id": inspection_id,
            "media_id": media.id,
            "detection_count": len(all_detections),
            "memory_count": persist_result["memory_count"],
            "db_count": persist_result["db_count"],
            "annotated_path": annotated_path,
            "detections": all_detections,
            "segmentation": segmentation_section,
            "depth": depth_section,
            "errors": pipeline_errors,
        }
        return summary

    try:
        if app is not None:
            with app.app_context():
                summary = _work()
        else:
            summary = _work()

        duration = time.monotonic() - started
        task_store.update(
            task_id,
            status=TASK_SUCCEEDED,
            progress=100.0,
            completed_at=datetime.utcnow(),
            result_summary={
                **{k: v for k, v in summary.items() if k != "detections"},
                "detections": summary["detections"],
                "duration_sec": round(duration, 3),
            },
            error_message=None,
        )
        logger.info(
            "Analysis SUCCESS task=%s inspection=%s detections=%s duration=%.3fs",
            task_id,
            inspection_id,
            summary["detection_count"],
            duration,
        )
    except DetectionError as exc:
        duration = time.monotonic() - started
        logger.exception("Analysis FAILED (media) task=%s", task_id)
        task_store.update(
            task_id,
            status=TASK_FAILED,
            completed_at=datetime.utcnow(),
            error_message=str(exc),
            result_summary={"duration_sec": round(duration, 3)},
        )
        if app is not None:
            with app.app_context():
                _update_media_status(media_id, MEDIA_FAILED)
    except Exception as exc:
        duration = time.monotonic() - started
        logger.exception("Analysis FAILED task=%s", task_id)
        task_store.update(
            task_id,
            status=TASK_FAILED,
            completed_at=datetime.utcnow(),
            error_message=str(exc),
            result_summary={"duration_sec": round(duration, 3)},
        )
        if app is not None:
            with app.app_context():
                _update_media_status(media_id, MEDIA_FAILED)
