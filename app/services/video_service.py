"""Video validation and keyframe extraction (OpenCV optional)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_video(video_path) -> dict:
    """
    Validate video codec/resolution/fps/duration.

    Returns:
        {'valid': True, 'resolution': '1920x1080', 'fps': 30, 'duration_sec': 45}
    """
    path = Path(video_path)

    if not path.exists() or not path.is_file():
        return {"valid": False, "error": "Video file was not found."}

    try:
        import cv2  # type: ignore
    except ImportError:
        size_mb = path.stat().st_size / (1024 * 1024)
        return {
            "valid": True,
            "resolution": "unknown",
            "fps": None,
            "duration_sec": None,
            "warning": "OpenCV is not installed; video metadata was not inspected.",
            "size_mb": round(size_mb, 2),
        }

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"valid": False, "error": "Unable to open video file."}

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    capture.release()

    duration = (frame_count / fps) if fps > 0 else None
    warnings: list[str] = []

    if width < 1280 or height < 720:
        warnings.append("Resolution is below 1280x720.")
    if fps and fps < 30:
        warnings.append("Frame rate is below 30 FPS.")

    return {
        "valid": True,
        "resolution": f"{width}x{height}",
        "fps": round(fps, 2) if fps else None,
        "duration_sec": round(duration, 2) if duration is not None else None,
        "warnings": warnings,
    }


def extract_keyframes(video_path, output_dir, fps: float = 1) -> list[str]:
    """
    Extract keyframes from a video (approximately 1 frame per second).

    Returns a list of extracted frame file paths.
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is required for keyframe extraction. "
            "Install opencv-python-headless."
        ) from exc

    source = Path(video_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError(f"Unable to open video: {source}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 30.0
    interval = max(int(round(source_fps / max(fps, 0.1))), 1)

    frame_paths: list[str] = []
    index = 0
    saved = 0

    while True:
        ok, frame = capture.read()
        if not ok:
            break

        if index % interval == 0:
            out_path = destination / f"frame_{saved:05d}.jpg"
            cv2.imwrite(str(out_path), frame)
            frame_paths.append(str(out_path))
            saved += 1

        index += 1

    capture.release()
    logger.info(
        "Extracted %s keyframes from %s",
        len(frame_paths),
        source.name,
    )
    return frame_paths
