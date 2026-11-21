from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


def allowed_file(
    uploaded_file: FileStorage,
    image_extensions: set[str],
    video_extensions: set[str]
) -> bool:
    if uploaded_file is None or not uploaded_file.filename:
        return False

    extension = Path(uploaded_file.filename).suffix.lower().lstrip(".")

    return extension in image_extensions or extension in video_extensions


def get_media_type(filename: str) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")

    if extension in {"jpg", "jpeg", "png"}:
        return "image"

    if extension in {"mp4", "mov"}:
        return "video"

    return "unknown"


def safe_original_filename(filename: str) -> str:
    return secure_filename(filename)


def validate_upload(
    uploaded_file: FileStorage,
    image_extensions: set[str],
    video_extensions: set[str]
) -> tuple[bool, str]:
    if uploaded_file is None:
        return False, "Please choose an image or video file."

    if not uploaded_file.filename:
        return False, "Please choose an image or video file."

    if not allowed_file(
        uploaded_file,
        image_extensions,
        video_extensions
    ):
        return (
            False,
            "Unsupported file type. Allowed: JPG, JPEG, PNG, MP4, MOV."
        )

    return True, ""