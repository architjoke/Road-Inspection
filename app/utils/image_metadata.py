from pathlib import Path

from PIL import Image, UnidentifiedImageError


def get_image_dimensions(file_path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(file_path) as image:
            return image.width, image.height
    except (UnidentifiedImageError, OSError):
        return None, None